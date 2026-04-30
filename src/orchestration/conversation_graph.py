from __future__ import annotations
import asyncio
import json
import logging

from src.llm.system_prompt import build_system_prompt
from src.models.session import ConversationMessage, SessionState
from src.orchestration.conversation_node import run_conversation_node
from src.orchestration.tool_executor_node import run_tool_executor_node
from src.tools.registry import tool_registry
from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 5


def _build_messages(state: SessionState) -> list[dict]:
    messages = [{"role": "system", "content": build_system_prompt()}]
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
    state.user_interrupted = False
    interrupted.clear()

    state.messages.append(ConversationMessage(role="user", content=user_text))

    tools = tool_registry.get_openai_schema()

    for round_num in range(_MAX_TOOL_ROUNDS):
        messages = _build_messages(state)

        try:
            full_reply, tool_calls = await run_conversation_node(
                state, messages, tools, sender, interrupted
            )
        except Exception:
            logger.exception("LLM stream error callSid=%s round=%d", state.call_sid, round_num)
            await sender.speak("I'm sorry, something went wrong. Please hold.", last=True)
            return

        if not tool_calls:
            await sender.speak("", last=True)
            logger.info("turn complete callSid=%s rounds=%d reply_len=%d", state.call_sid, round_num + 1, len(full_reply))
            return

        await run_tool_executor_node(state, tool_calls)

    logger.warning("hit max tool rounds callSid=%s", state.call_sid)
    await sender.speak("I'm sorry, I wasn't able to complete that. Let me transfer you to someone who can help.", last=True)
