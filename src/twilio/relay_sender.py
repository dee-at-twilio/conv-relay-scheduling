from __future__ import annotations
import json
import logging
from fastapi import WebSocket

from src.models.relay import (
    TextMessage, PlayMessage, SendDigitsMessage, LanguageMessage, EndSessionMessage,
)

logger = logging.getLogger(__name__)


class ConversationRelaySender:
    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def speak(
        self,
        token: str,
        last: bool = False,
        lang: str | None = None,
        interruptible: bool = True,
        preemptible: bool = False,
    ) -> None:
        msg = TextMessage(token=token, last=last, lang=lang, interruptible=interruptible, preemptible=preemptible)
        await self._send(msg.model_dump(exclude_none=True))

    async def play_media(
        self,
        source: str,
        loop: int = 1,
        interruptible: bool = True,
        preemptible: bool = False,
    ) -> None:
        msg = PlayMessage(source=source, loop=loop, interruptible=interruptible, preemptible=preemptible)
        await self._send(msg.model_dump())

    async def send_digits(self, digits: str) -> None:
        msg = SendDigitsMessage(digits=digits)
        await self._send(msg.model_dump())

    async def switch_language(self, tts_language: str, transcription_language: str) -> None:
        msg = LanguageMessage(ttsLanguage=tts_language, transcriptionLanguage=transcription_language)
        await self._send(msg.model_dump())

    async def end_session(self, handoff_data: dict | None = None) -> None:
        import json as _json
        msg = EndSessionMessage(
            handoffData=_json.dumps(handoff_data) if handoff_data else None
        )
        await self._send(msg.model_dump(exclude_none=True))

    async def _send(self, payload: dict) -> None:
        text = json.dumps(payload)
        logger.debug("→ Twilio: %s", text)
        await self._ws.send_text(text)
