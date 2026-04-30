from __future__ import annotations
import asyncio
import json
import logging

from src.events.event_bus import event_bus
from src.events.event_types import TranscriptEvent
from src.llm.llm_service import stream_completion, TextToken, ToolCallEvent
from src.models.session import ConversationMessage, SessionState
from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)

# Sentence-ending punctuation — publish a transcript event when we hit one.
_SENTENCE_ENDS = {".", "!", "?"}


async def run_conversation_node(
    state: SessionState,
    messages: list[dict],
    tools: list[dict],
    sender: ConversationRelaySender,
    interrupted: asyncio.Event,
) -> tuple[str, list[ToolCallEvent]]:
    reply_tokens: list[str] = []
    tool_calls: list[ToolCallEvent] = []
    sentence_buf: list[str] = []

    async for event in stream_completion(messages, tools=tools, interrupted=interrupted):
        if isinstance(event, TextToken):
            reply_tokens.append(event.content)
            sentence_buf.append(event.content)
            await sender.speak(event.content, last=False)

            # Flush a sentence to the UI whenever we hit terminal punctuation
            joined = "".join(sentence_buf)
            if any(joined.rstrip().endswith(p) for p in _SENTENCE_ENDS):
                sentence = joined.strip()
                if sentence:
                    event_bus.publish(TranscriptEvent(call_sid=state.call_sid, role="assistant", text=sentence))
                sentence_buf = []

        elif isinstance(event, ToolCallEvent):
            tool_calls.append(event)

    # Flush any trailing text that didn't end with punctuation
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

    return full_reply, tool_calls
