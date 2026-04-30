from __future__ import annotations
import asyncio
import json
import logging
from fastapi import WebSocket, WebSocketDisconnect

from src.models.relay import parse_inbound, SetupMessage, PromptMessage, DTMFMessage, InterruptMessage, ErrorMessage
from src.orchestration.conversation_graph import process_turn
from src.session.session_manager import session_manager
from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)


async def handle_relay_websocket(ws: WebSocket) -> None:
    await ws.accept()
    sender = ConversationRelaySender(ws)
    call_sid: str | None = None
    interrupted = asyncio.Event()

    try:
        async for raw in ws.iter_text():
            logger.debug("← Twilio: %s", raw)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("invalid JSON from relay: %s", raw)
                continue

            msg = parse_inbound(data)

            match msg:
                case SetupMessage():
                    call_sid = msg.callSid
                    session_manager.init_session(
                        call_sid,
                        from_number=msg.from_ or "",
                        to_number=msg.to or "",
                    )

                case PromptMessage():
                    if not call_sid:
                        logger.warning("prompt received before setup")
                        continue
                    state = session_manager.get_session(call_sid)
                    if not state:
                        logger.warning("no session for callSid=%s", call_sid)
                        continue
                    logger.info("prompt callSid=%s text=%r", call_sid, msg.voicePrompt)
                    asyncio.create_task(
                        process_turn(state, msg.voicePrompt, sender, interrupted)
                    )

                case InterruptMessage():
                    logger.info("interrupt callSid=%s", call_sid)
                    interrupted.set()
                    if call_sid:
                        state = session_manager.get_session(call_sid)
                        if state:
                            state.user_interrupted = True

                case DTMFMessage():
                    logger.info("dtmf callSid=%s digit=%s", call_sid, msg.digit)

                case ErrorMessage():
                    logger.error("relay error callSid=%s desc=%s", call_sid, msg.description)

                case None:
                    logger.warning("unknown relay message type: %s", data.get("type"))

    except WebSocketDisconnect:
        logger.info("relay WS disconnected callSid=%s", call_sid)
    finally:
        if call_sid:
            session_manager.end_session(call_sid)
