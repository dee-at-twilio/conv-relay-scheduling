from __future__ import annotations
import logging
from src.models.session import SessionState

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, SessionState] = {}

    def init_session(self, call_sid: str, from_number: str, to_number: str) -> SessionState:
        state = SessionState(call_sid=call_sid, from_number=from_number, to_number=to_number)
        self._sessions[call_sid] = state
        logger.info("session init callSid=%s from=%s", call_sid, from_number)
        return state

    def get_session(self, call_sid: str) -> SessionState | None:
        return self._sessions.get(call_sid)

    def end_session(self, call_sid: str) -> None:
        self._sessions.pop(call_sid, None)
        logger.info("session ended callSid=%s", call_sid)


session_manager = SessionManager()
