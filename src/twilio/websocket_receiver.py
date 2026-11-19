from __future__ import annotations
import asyncio
import json
import logging
from fastapi import WebSocket, WebSocketDisconnect
from opentelemetry import trace

from src import bluejay
from src.events.event_bus import event_bus
from src.events.event_types import SessionEvent, TranscriptEvent
from src.llm.translate import translate
from src.models.relay import parse_inbound, SetupMessage, PromptMessage, DTMFMessage, InterruptMessage, ErrorMessage
from src.models.session import SessionState
from src.orchestration.conversation_graph import process_turn
from src.session.session_manager import session_manager
from src.twilio.bridge_manager import BridgeParticipant, bridge_manager
from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def handle_relay_websocket(ws: WebSocket) -> None:
    await ws.accept()
    sender = ConversationRelaySender(ws)

    # Peek the first message (setup) to decide which loop to run
    try:
        raw = await ws.receive_text()
    except WebSocketDisconnect:
        logger.info("relay WS closed before setup")
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("invalid JSON on first frame: %s", raw)
        return

    setup = parse_inbound(data)
    if not isinstance(setup, SetupMessage):
        logger.warning("expected setup as first frame, got %s", type(setup).__name__)
        return

    params = setup.customParameters or {}
    if params.get("session_id") and params.get("role"):
        await _run_bridge_loop(ws, sender, setup)
    else:
        with tracer.start_as_current_span("call", attributes={"call.sid": setup.callSid}) as call_span:
            await _run_agent_loop(ws, sender, setup)
        await bluejay.link_simulation(call_span, setup.callSid)


async def _run_agent_loop(ws: WebSocket, sender: ConversationRelaySender, setup: SetupMessage) -> None:
    call_sid = setup.callSid
    state = session_manager.init_session(
        call_sid,
        from_number=setup.from_ or "",
        to_number=setup.to or "",
    )
    session_manager.register_sender(call_sid, sender)
    event_bus.publish(SessionEvent(call_sid=call_sid, event="started", from_number=setup.from_ or ""))
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
                case PromptMessage():
                    logger.info("prompt callSid=%s text=%r", call_sid, msg.voicePrompt)
                    event_bus.publish(TranscriptEvent(call_sid=call_sid, role="user", text=msg.voicePrompt))
                    asyncio.create_task(process_turn(state, msg.voicePrompt, sender, interrupted))

                case InterruptMessage():
                    logger.info("interrupt callSid=%s", call_sid)
                    interrupted.set()
                    state.user_interrupted = True

                case DTMFMessage():
                    logger.info("dtmf callSid=%s digit=%s", call_sid, msg.digit)

                case ErrorMessage():
                    logger.error("relay error callSid=%s desc=%s", call_sid, msg.description)

                case SetupMessage():
                    logger.warning("unexpected second setup callSid=%s", call_sid)

                case None:
                    logger.warning("unknown relay message type: %s", data.get("type"))

    except WebSocketDisconnect:
        logger.info("relay WS disconnected callSid=%s", call_sid)
    finally:
        bluejay.set_metadata(call_sid, _call_metadata(state))
        session_manager.end_session(call_sid)
        event_bus.publish(SessionEvent(call_sid=call_sid, event="ended"))


_OUTCOMES = {"schedule_appointment": "booked", "reschedule_appointment": "rescheduled", "cancel_appointment": "cancelled"}


def _call_metadata(state: SessionState) -> dict:
    """Summarize the call for Bluejay metric prompts. Leaves out names and phone numbers (PII)."""
    results = [json.loads(m.content) for m in state.messages if m.role == "tool"]
    succeeded = {r["tool_name"] for r in results if r["success"]}
    return {
        "outcome": ", ".join(o for tool, o in _OUTCOMES.items() if tool in succeeded) or "no_action",
        "patient_identified": state.patient_id is not None,
        "final_language": state.language,
        "turns": sum(m.role == "user" for m in state.messages),
        "tool_failures": sum(not r["success"] for r in results),
    }


async def _run_bridge_loop(ws: WebSocket, sender: ConversationRelaySender, setup: SetupMessage) -> None:
    params = setup.customParameters or {}
    session_id = str(params["session_id"])
    role = str(params["role"])
    greeting_lang = str(params.get("greeting_lang") or "en-US")
    call_sid = setup.callSid

    participant = BridgeParticipant(
        session_id=session_id,
        role=role,
        sender=sender,
        call_sid=call_sid,
        greeting_lang=greeting_lang,
    )
    bridge_manager.register(participant)
    event_bus.publish(SessionEvent(call_sid=call_sid, event="started", from_number=setup.from_ or ""))
    logger.info("bridge setup session=%s role=%s callSid=%s", session_id, role, call_sid)

    try:
        async for raw in ws.iter_text():
            logger.debug("← Twilio (bridge %s/%s): %s", session_id, role, raw)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("invalid JSON from relay: %s", raw)
                continue

            msg = parse_inbound(data)

            match msg:
                case PromptMessage():
                    source_lang = (msg.lang or greeting_lang).split("-")[0]
                    participant.last_spoken_lang = source_lang
                    event_bus.publish(TranscriptEvent(call_sid=call_sid, role="user", text=msg.voicePrompt))

                    peer = bridge_manager.peer(session_id, role)
                    if not peer:
                        logger.info("bridge no peer yet session=%s role=%s — dropping prompt", session_id, role)
                        continue

                    target_lang = (peer.preferred_lang or "en-US").split("-")[0]
                    asyncio.create_task(_forward_prompt(msg.voicePrompt, source_lang, target_lang, peer))

                case InterruptMessage():
                    logger.info("bridge interrupt session=%s role=%s", session_id, role)

                case ErrorMessage():
                    logger.error("bridge relay error session=%s role=%s desc=%s", session_id, role, msg.description)

                case SetupMessage():
                    logger.warning("unexpected second setup on bridge session=%s role=%s", session_id, role)

                case DTMFMessage():
                    logger.info("bridge dtmf session=%s role=%s digit=%s", session_id, role, msg.digit)

                case None:
                    logger.warning("unknown relay message type: %s", data.get("type"))

    except WebSocketDisconnect:
        logger.info("bridge WS disconnected session=%s role=%s", session_id, role)
    finally:
        bridge_manager.unregister(session_id, role)
        event_bus.publish(SessionEvent(call_sid=call_sid, event="ended"))


async def _forward_prompt(text: str, source_lang: str, target_lang: str, peer: BridgeParticipant) -> None:
    try:
        translated = await translate(text, source_lang, target_lang)
    except Exception:
        logger.exception("translate failed session=%s peer_role=%s", peer.session_id, peer.role)
        return
    event_bus.publish(TranscriptEvent(call_sid=peer.call_sid, role="assistant", text=translated))
    try:
        await peer.sender.speak(translated, last=True, lang="multi")
    except Exception:
        logger.exception("speak to peer failed session=%s peer_role=%s", peer.session_id, peer.role)
