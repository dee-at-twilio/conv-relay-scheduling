from __future__ import annotations
import asyncio
import logging

from src.llm.llm_service import stream_completion
from src.llm.system_prompt import build_system_prompt
from src.models.session import ConversationMessage, SessionState
from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)


async def process_turn(
    state: SessionState,
    user_text: str,
    sender: ConversationRelaySender,
    interrupted: asyncio.Event,
) -> None:
    """
    Appends the user utterance to state, calls the LLM, streams tokens back
    to the caller via sender, then appends the assistant reply to state.
    """
    state.user_interrupted = False
    interrupted.clear()

    state.messages.append(ConversationMessage(role="user", content=user_text))

    messages = [{"role": "system", "content": build_system_prompt()}]
    for m in state.messages:
        msg: dict = {"role": m.role, "content": m.content}
        messages.append(msg)

    reply_tokens: list[str] = []

    try:
        async for token in stream_completion(messages, interrupted=interrupted):
            reply_tokens.append(token)
            await sender.speak(token, last=False)

        # send final token with last=True so Twilio knows the turn is done
        if reply_tokens:
            await sender.speak("", last=True)

    except Exception:
        logger.exception("LLM stream error callSid=%s", state.call_sid)
        await sender.speak("I'm sorry, something went wrong. Please hold.", last=True)
        return

    full_reply = "".join(reply_tokens)
    if full_reply:
        state.messages.append(ConversationMessage(role="assistant", content=full_reply))
        logger.info("turn complete callSid=%s reply_len=%d", state.call_sid, len(full_reply))
