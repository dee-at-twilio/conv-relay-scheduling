from __future__ import annotations
import asyncio
import json
import logging

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace

from src.events.event_bus import event_bus
from src.events.event_types import TranscriptEvent
from src.events.event_types import ToolCallEvent as ToolCallUIEvent
from src.llm.llm_service import stream_completion, TextToken, ToolCallEvent
from src.llm.system_prompt import build_system_prompt
from src.models.session import ConversationMessage, SessionState
from src.tools.registry import tool_registry
from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_MAX_TOOL_ROUNDS = 5
_SENTENCE_ENDS = {".", "!", "?"}


def _build_messages(state: SessionState) -> list[dict]:
    messages = [{"role": "system", "content": build_system_prompt(state.language)}]
    for m in state.messages:
        if m.role == "assistant" and m.content and m.content.startswith('{"tool_calls":'):
            payload = json.loads(m.content)
            messages.append({"role": "assistant", "content": None, "tool_calls": payload["tool_calls"]})
        elif m.role == "tool":
            messages.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        else:
            messages.append({"role": m.role, "content": m.content or ""})
    return messages


async def process_turn(
    state: SessionState,
    user_text: str,
    sender: ConversationRelaySender,
    interrupted: asyncio.Event,
) -> None:
    with tracer.start_as_current_span("turn", attributes={
        SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.CHAIN.value,
        SpanAttributes.SESSION_ID: state.call_sid,
        SpanAttributes.INPUT_VALUE: user_text,
    }) as span:
        await _run_turn(state, user_text, sender, interrupted)
        last = state.messages[-1] if state.messages else None
        if last and last.role == "assistant" and last.content:
            span.set_attribute(SpanAttributes.OUTPUT_VALUE, last.content)
        span.set_attribute("turn.interrupted", state.user_interrupted)


async def _run_turn(
    state: SessionState,
    user_text: str,
    sender: ConversationRelaySender,
    interrupted: asyncio.Event,
) -> None:
    state.user_interrupted = False
    interrupted.clear()
    state.messages.append(ConversationMessage(role="user", content=user_text))
    tools = tool_registry.get_openai_schema()

    for round_num in range(_MAX_TOOL_ROUNDS):
        messages = _build_messages(state)
        reply_tokens: list[str] = []
        tool_calls: list[ToolCallEvent] = []
        sentence_buf: list[str] = []

        try:
            async for event in stream_completion(messages, tools=tools, interrupted=interrupted):
                if isinstance(event, TextToken):
                    reply_tokens.append(event.content)
                    sentence_buf.append(event.content)
                    await sender.speak(event.content, last=False)
                    joined = "".join(sentence_buf)
                    if any(joined.rstrip().endswith(p) for p in _SENTENCE_ENDS):
                        sentence = joined.strip()
                        if sentence:
                            event_bus.publish(TranscriptEvent(call_sid=state.call_sid, role="assistant", text=sentence))
                        sentence_buf = []
                elif isinstance(event, ToolCallEvent):
                    tool_calls.append(event)
        except Exception:
            logger.exception("LLM stream error callSid=%s round=%d", state.call_sid, round_num)
            await sender.speak("I'm sorry, something went wrong. Please hold.", last=True)
            return

        remainder = "".join(sentence_buf).strip()
        if remainder:
            event_bus.publish(TranscriptEvent(call_sid=state.call_sid, role="assistant", text=remainder))

        full_reply = "".join(reply_tokens)
        if full_reply:
            state.messages.append(ConversationMessage(role="assistant", content=full_reply))

        if tool_calls:
            tool_calls_payload = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in tool_calls
            ]
            state.messages.append(ConversationMessage(
                role="assistant",
                content=json.dumps({"tool_calls": tool_calls_payload}),
            ))

        if not tool_calls:
            await sender.speak("", last=True)
            logger.info("turn complete callSid=%s rounds=%d reply_len=%d", state.call_sid, round_num + 1, len(full_reply))
            return

        for tc in tool_calls:
            logger.info("executing tool=%s callSid=%s", tc.name, state.call_sid)
            result = await tool_registry.run(tc.name, tc.arguments, state)
            event_bus.publish(ToolCallUIEvent(
                call_sid=state.call_sid,
                tool_name=tc.name,
                arguments=tc.arguments,
                result=result.data,
                success=result.success,
            ))
            state.messages.append(ConversationMessage(
                role="tool",
                content=json.dumps(result.model_dump()),
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
    await sender.speak("I'm sorry, I wasn't able to complete that. Let me transfer you to someone who can help.", last=True)
