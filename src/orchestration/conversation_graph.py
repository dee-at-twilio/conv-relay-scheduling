from __future__ import annotations
import asyncio
import json
import logging

from src.llm.llm_service import stream_completion, TextToken, ToolCallEvent
from src.llm.system_prompt import build_system_prompt
from src.models.session import ConversationMessage, SessionState
from src.tools.registry import tool_registry
from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 5  # guard against infinite tool loops


def _build_messages(state: SessionState) -> list[dict]:
    messages = [{"role": "system", "content": build_system_prompt()}]
    for m in state.messages:
        # Assistant messages that contain tool_calls are stored as JSON in content
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
    state.user_interrupted = False
    interrupted.clear()

    state.messages.append(ConversationMessage(role="user", content=user_text))

    tools = tool_registry.get_openai_schema()

    for round_num in range(_MAX_TOOL_ROUNDS):
        messages = _build_messages(state)
        reply_tokens: list[str] = []
        tool_calls_this_round: list[ToolCallEvent] = []

        try:
            async for event in stream_completion(messages, tools=tools, interrupted=interrupted):
                if isinstance(event, TextToken):
                    reply_tokens.append(event.content)
                    await sender.speak(event.content, last=False)
                elif isinstance(event, ToolCallEvent):
                    tool_calls_this_round.append(event)
        except Exception:
            logger.exception("LLM stream error callSid=%s round=%d", state.call_sid, round_num)
            await sender.speak("I'm sorry, something went wrong. Please hold.", last=True)
            return

        # Persist the assistant turn
        full_reply = "".join(reply_tokens)
        if full_reply:
            state.messages.append(ConversationMessage(role="assistant", content=full_reply))

        # No tool calls → turn is done, close the speak stream
        if not tool_calls_this_round:
            await sender.speak("", last=True)
            logger.info("turn complete callSid=%s rounds=%d reply_len=%d", state.call_sid, round_num + 1, len(full_reply))
            return

        # Store the assistant tool-call request in history (required by OpenAI)
        # OpenAI expects a single assistant message with tool_calls array; we store
        # the raw JSON so _build_messages can pass it through.
        tool_calls_payload = [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
            for tc in tool_calls_this_round
        ]
        state.messages.append(ConversationMessage(
            role="assistant",
            content=json.dumps({"tool_calls": tool_calls_payload}),
        ))

        # Execute each tool and store results
        for tc in tool_calls_this_round:
            logger.info("executing tool=%s callSid=%s", tc.name, state.call_sid)
            result = await tool_registry.run(tc.name, tc.arguments, state)
            state.messages.append(ConversationMessage(
                role="tool",
                content=json.dumps(result.model_dump()),
                tool_call_id=tc.id,
                tool_name=tc.name,
            ))

    logger.warning("hit max tool rounds callSid=%s", state.call_sid)
    await sender.speak("I'm sorry, I wasn't able to complete that. Let me transfer you to someone who can help.", last=True)
