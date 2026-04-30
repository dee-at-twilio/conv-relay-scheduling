from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TranscriptEvent:
    call_sid: str
    role: str          # "user" | "assistant"
    text: str          # complete sentence (assistant) or full utterance (user)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ToolCallEvent:
    call_sid: str
    tool_name: str
    arguments: dict
    result: dict | None = None   # None while running, populated after
    success: bool | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SessionEvent:
    call_sid: str
    event: str         # "started" | "ended"
    from_number: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
