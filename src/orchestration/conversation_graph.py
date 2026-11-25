from __future__ import annotations
import asyncio
import json
import logging
import time

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from src.events.event_bus import event_bus
from src.events.event_types import TranscriptEvent
from src.events.event_types import ToolCallEvent as ToolCallUIEvent
from src.llm.llm_service import stream_completion, TextToken, ToolCallEvent
from src.llm.system_prompt import build_system_prompt
from src.models.session import ConversationMessage, SessionState
from src.tools.registry import tool_registry
from src.twilio.relay_sender import ConversationRelaySender
from src.twilio.turn_timeline import VoiceTurn

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_MAX_TOOL_ROUNDS = 5
_SENTENCE_ENDS = {".", "!", "?"}


def _build_messages(state: SessionState) -> list[dict]:
    """
    OpenAI requires every assistant tool_call to be answered by a tool message directly after it.
    Overlapping turns can interleave other messages into the shared history, or read it while a tool
    is still running, so tool results are re-attached to their call and unanswered calls are left out.
    """
    results = {m.tool_call_id: m.content or "" for m in state.messages if m.role == "tool" and m.tool_call_id}
    messages = [{"role": "system", "content": build_system_prompt(state.language)}]
    for m in state.messages:
        if m.role == "assistant" and m.content and m.content.startswith('{"tool_calls":'):
            answered = [tc for tc in json.loads(m.content)["tool_calls"] if tc["id"] in results]
            if not answered:
                continue
            messages.append({"role": "assistant", "content": None, "tool_calls": answered})
            messages.extend({"role": "tool", "tool_call_id": tc["id"], "content": results[tc["id"]]} for tc in answered)
        elif m.role == "tool":
            continue  # emitted right after its tool_calls message above
        else:
            messages.append({"role": m.role, "content": m.content or ""})
    return messages


async def process_turn(
    state: SessionState,
    user_text: str,
    sender: ConversationRelaySender,
    interrupted: asyncio.Event,
    turn: VoiceTurn,
) -> None:
    # The turn span is owned by TurnTimeline (it outlives processing until the agent stops speaking);
    # make it current here so llm and tool spans nest under it.
    with trace.use_span(turn.span, end_on_exit=False):
        turn.processing_started(len(state.messages))
        try:
            await _run_turn(state, user_text, sender, interrupted, turn)
            if turn.sent_text:
                turn.span.set_attribute(SpanAttributes.OUTPUT_VALUE, turn.sent_text)
        except Exception as exc:
            # Runs as a fire-and-forget task: without this the error is only "never retrieved" and the caller hears silence
            logger.exception("turn failed callSid=%s", state.call_sid)
            turn.span.record_exception(exc)
            turn.span.set_status(Status(StatusCode.ERROR, f"turn failed: {type(exc).__name__}"))
            try:
                await _say(sender, turn, "I'm sorry, something went wrong. Could you say that again?", last=True)
            except Exception:
                logger.exception("could not send apology callSid=%s", state.call_sid)
        finally:
            turn.processing_finished(len(state.messages))


def _add(state: SessionState, turn: VoiceTurn, message: ConversationMessage) -> None:
    """Append to the shared history, counting it as this turn's (so traces can spot other turns' writes)."""
    state.messages.append(message)
    turn.message_added()


async def _say(sender: ConversationRelaySender, turn: VoiceTurn, text: str, last: bool) -> None:
    await sender.speak(text, last=last)
    turn.text_sent(text, last)


async def _run_turn(
    state: SessionState,
    user_text: str,
    sender: ConversationRelaySender,
    interrupted: asyncio.Event,
    turn: VoiceTurn,
) -> None:
    state.user_interrupted = False
    interrupted.clear()
    _add(state, turn, ConversationMessage(role="user", content=user_text))
    tools = tool_registry.get_openai_schema()

    for round_num in range(_MAX_TOOL_ROUNDS):
        messages = _build_messages(state)
        turn.llm_round(round_num, len(state.messages))
        reply_tokens: list[str] = []
        tool_calls: list[ToolCallEvent] = []
        sentence_buf: list[str] = []
        llm_started = time.perf_counter()

        try:
            async for event in stream_completion(messages, tools=tools, interrupted=interrupted):
                if isinstance(event, TextToken):
                    reply_tokens.append(event.content)
                    sentence_buf.append(event.content)
                    await _say(sender, turn, event.content, last=False)
                    joined = "".join(sentence_buf)
                    if any(joined.rstrip().endswith(p) for p in _SENTENCE_ENDS):
                        sentence = joined.strip()
                        if sentence:
                            event_bus.publish(TranscriptEvent(call_sid=state.call_sid, role="assistant", text=sentence))
                        sentence_buf = []
                elif isinstance(event, ToolCallEvent):
                    tool_calls.append(event)
        except Exception as exc:
            logger.exception("LLM stream error callSid=%s round=%d", state.call_sid, round_num)
            turn.span.record_exception(exc)
            turn.span.set_status(Status(StatusCode.ERROR, "LLM stream error"))
            await _say(sender, turn, "I'm sorry, something went wrong. Please hold.", last=True)
            return
        finally:
            turn.add_llm_ms((time.perf_counter() - llm_started) * 1000)

        remainder = "".join(sentence_buf).strip()
        if remainder:
            event_bus.publish(TranscriptEvent(call_sid=state.call_sid, role="assistant", text=remainder))

        full_reply = "".join(reply_tokens)
        if full_reply:
            _add(state, turn, ConversationMessage(role="assistant", content=full_reply))

        if tool_calls:
            tool_calls_payload = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in tool_calls
            ]
            _add(state, turn, ConversationMessage(
                role="assistant",
                content=json.dumps({"tool_calls": tool_calls_payload}),
            ))

        if not tool_calls:
            await _say(sender, turn, "", last=True)
            logger.info("turn complete callSid=%s rounds=%d reply_len=%d", state.call_sid, round_num + 1, len(full_reply))
            return

        for tc in tool_calls:
            logger.info("executing tool=%s callSid=%s", tc.name, state.call_sid)
            with tracer.start_as_current_span(f"tool: {tc.name}", attributes={
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.TOOL.value,
                SpanAttributes.TOOL_NAME: tc.name,
                SpanAttributes.INPUT_VALUE: json.dumps(tc.arguments),
                SpanAttributes.INPUT_MIME_TYPE: "application/json",
            }) as tool_span:
                tool_started = time.perf_counter()
                try:
                    result = await tool_registry.run(tc.name, tc.arguments, state)
                finally:
                    turn.add_tools_ms((time.perf_counter() - tool_started) * 1000)
                result_json = json.dumps(result.model_dump())
                tool_span.set_attribute(SpanAttributes.OUTPUT_VALUE, result_json)
                tool_span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, "application/json")
                tool_span.set_attribute("tool.success", result.success)
                if not result.success:
                    tool_span.set_status(Status(StatusCode.ERROR, result.error or "tool failed"))
            event_bus.publish(ToolCallUIEvent(
                call_sid=state.call_sid,
                tool_name=tc.name,
                arguments=tc.arguments,
                result=result.data,
                success=result.success,
            ))
            _add(state, turn, ConversationMessage(
                role="tool",
                content=result_json,
                tool_call_id=tc.id,
                tool_name=tc.name,
            ))

        if state.pending_language_switch:
            new_lang = state.pending_language_switch
            state.pending_language_switch = None
            logger.info("switching language callSid=%s %s→%s", state.call_sid, state.language, new_lang)
            await sender.switch_language(new_lang, new_lang)
            state.language = new_lang

    logger.warning("hit max tool rounds callSid=%s", state.call_sid)
    await _say(sender, turn, "I'm sorry, I wasn't able to complete that. Let me transfer you to someone who can help.", last=True)
