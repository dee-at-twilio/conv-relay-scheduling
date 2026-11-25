from __future__ import annotations
import asyncio
import json
import logging
from fastapi import WebSocket, WebSocketDisconnect
from openinference.instrumentation import using_attributes
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from src.config import config
from src.events.event_bus import event_bus
from src.events.event_types import SessionEvent, TranscriptEvent
from src.llm.translate import translate
from src.models.relay import (
    parse_inbound, SetupMessage, PromptMessage, DTMFMessage, InterruptMessage, InfoMessage, ErrorMessage,
)
from src.models.session import SessionState
from src.orchestration.conversation_graph import process_turn
from src.session.session_manager import session_manager
from src.twilio.bridge_manager import BridgeParticipant, bridge_manager
from src.twilio.relay_sender import ConversationRelaySender
from src.twilio.turn_timeline import TurnTimeline

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

    # One span per call; turns and LLM calls (spawned via create_task) inherit it as parent.
    # Exceptions mark the span ERROR automatically; a normal hang-up marks it OK.
    params = setup.customParameters or {}
    caller = setup.from_ or ""
    if params.get("session_id") and params.get("role"):
        session_id = str(params["session_id"])
        extra = {"bridge.role": str(params["role"]), "bridge.greeting_lang": str(params.get("greeting_lang") or "en-US")}
        with using_attributes(session_id=session_id, user_id=caller), \
                _call_span("bridge_call", setup, session_id, extra) as span:
            await _run_bridge_loop(ws, sender, setup)
            span.set_status(StatusCode.OK)
    else:
        with using_attributes(session_id=setup.callSid, user_id=caller), \
                _call_span("call", setup, setup.callSid) as span:
            await _run_agent_loop(ws, sender, setup)
            span.set_status(StatusCode.OK)


def _call_span(name: str, setup: SetupMessage, session_id: str, extra: dict[str, str] | None = None):
    return tracer.start_as_current_span(name, attributes={
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.AGENT.value,
        SpanAttributes.SESSION_ID: session_id,
        SpanAttributes.USER_ID: setup.from_ or "",
        SpanAttributes.LLM_MODEL_NAME: config.openai_model,
        "twilio.call_sid": setup.callSid,
        "twilio.from": setup.from_ or "",
        "twilio.to": setup.to or "",
        **(extra or {}),
    })


def _log_inbound(span: trace.Span, data: dict, raw: str) -> None:
    """Raw relay message log on the call span: the full inbound sequence, including messages outside any turn."""
    kind = data.get("type")
    name = f"← {kind}.{data['name']}" if kind == "info" and data.get("name") else f"← {kind}"
    span.add_event(name, {"raw": raw[:2000]})


# Tools whose success means the call achieved something
_ACTION_TOOLS = {"schedule_appointment", "reschedule_appointment", "cancel_appointment", "register_patient", "send_sms"}


def _record_call_summary(state: SessionState, greeting: str | None = None) -> None:
    """Fill the root span's input/output/metadata so each call is readable (and evaluable) from the Spans list."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    spoken = [
        m for m in state.messages
        if m.role in ("user", "assistant") and m.content and not m.content.startswith('{"tool_calls":')
    ]
    tool_results = [(m.tool_name, json.loads(m.content).get("success", False)) for m in state.messages if m.role == "tool"]
    tools_used = list(dict.fromkeys(name for name, _ in tool_results if name))
    actions = list(dict.fromkeys(name for name, ok in tool_results if ok and name in _ACTION_TOOLS))
    outcome = ", ".join(actions) or "no_action"
    turns = sum(1 for m in spoken if m.role == "user")
    metadata = {
        "outcome": outcome,
        "turns": turns,
        "tools_used": tools_used,
        "failed_tools": [name for name, ok in tool_results if not ok],
        "language": state.language,
        "patient_identified": state.patient_id is not None,
    }
    transcript = [f"agent (greeting): {greeting}"] if greeting else []
    transcript += [f"{'caller' if m.role == 'user' else 'agent'}: {m.content}" for m in spoken]
    span.set_attributes({
        SpanAttributes.INPUT_VALUE: next((m.content for m in spoken if m.role == "user"), ""),
        SpanAttributes.OUTPUT_VALUE: "\n".join(transcript),
        SpanAttributes.METADATA: json.dumps(metadata),
        "call.outcome": outcome,
        "call.turns": turns,
        "call.tools_used": tools_used,
        "call.language": state.language,
        "call.patient_identified": state.patient_id is not None,
    })


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
    call_span = trace.get_current_span()
    timeline = TurnTimeline(call_sid)
    greeting = (setup.customParameters or {}).get("welcome_greeting")
    if greeting:  # only set on the first connection, not on reconnects
        timeline.start_greeting(str(greeting))

    try:
        async for raw in ws.iter_text():
            logger.debug("← Twilio: %s", raw)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("invalid JSON from relay: %s", raw)
                continue

            msg = parse_inbound(data)
            _log_inbound(call_span, data, raw)

            match msg:
                case PromptMessage():
                    logger.info("prompt callSid=%s text=%r", call_sid, msg.voicePrompt)
                    event_bus.publish(TranscriptEvent(call_sid=call_sid, role="user", text=msg.voicePrompt))
                    turn = timeline.start_turn(msg.voicePrompt, msg.lang, msg.last, raw)  # here, so it nests under the call span
                    asyncio.create_task(process_turn(state, msg.voicePrompt, sender, interrupted, turn))

                case InfoMessage():
                    logger.debug("info callSid=%s name=%s value=%r", call_sid, msg.name, msg.value)
                    timeline.on_info(msg.name, msg.value)

                case InterruptMessage():
                    logger.info("interrupt callSid=%s", call_sid)
                    interrupted.set()
                    state.user_interrupted = True
                    timeline.on_interrupt(msg.utteranceUntilInterrupt, msg.durationUntilInterruptMs)

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
        session_manager.end_session(call_sid)
        event_bus.publish(SessionEvent(call_sid=call_sid, event="ended"))
        timeline.end_call()
        _record_call_summary(state, timeline.greeting_text)


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
    call_span = trace.get_current_span()
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
            _log_inbound(call_span, data, raw)

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
