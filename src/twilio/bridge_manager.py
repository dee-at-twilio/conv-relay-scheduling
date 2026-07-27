from __future__ import annotations
import logging
from dataclasses import dataclass, field

from src.twilio.relay_sender import ConversationRelaySender

logger = logging.getLogger(__name__)


@dataclass
class BridgeParticipant:
    session_id: str
    role: str  # "A" | "B"
    sender: ConversationRelaySender
    call_sid: str
    greeting_lang: str = "en-US"  # UI-supplied hint for the first translation direction
    last_spoken_lang: str | None = None  # updated on each prompt (BCP-47 primary tag)

    @property
    def preferred_lang(self) -> str:
        return self.last_spoken_lang or self.greeting_lang


class BridgeManager:
    def __init__(self) -> None:
        # session_id -> { role -> BridgeParticipant }
        self._sessions: dict[str, dict[str, BridgeParticipant]] = {}

    def register(self, p: BridgeParticipant) -> None:
        self._sessions.setdefault(p.session_id, {})[p.role] = p
        logger.info("bridge register session=%s role=%s callSid=%s", p.session_id, p.role, p.call_sid)

    def peer(self, session_id: str, role: str) -> BridgeParticipant | None:
        parts = self._sessions.get(session_id, {})
        for other_role, participant in parts.items():
            if other_role != role:
                return participant
        return None

    def get(self, session_id: str, role: str) -> BridgeParticipant | None:
        return self._sessions.get(session_id, {}).get(role)

    def unregister(self, session_id: str, role: str) -> None:
        parts = self._sessions.get(session_id)
        if not parts:
            return
        parts.pop(role, None)
        if not parts:
            self._sessions.pop(session_id, None)
        logger.info("bridge unregister session=%s role=%s", session_id, role)


bridge_manager = BridgeManager()
