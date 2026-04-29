from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_name: str | None = None


class SessionState(BaseModel):
    call_sid: str
    from_number: str
    to_number: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    messages: list[ConversationMessage] = []
    user_interrupted: bool = False
    handoff_required: bool = False
    handoff_data: dict | None = None
    language: str = "en-US"
