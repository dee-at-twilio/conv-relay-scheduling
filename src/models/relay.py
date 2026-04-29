from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel


# ── Inbound messages FROM Twilio ConversationRelay ──────────────────────────

class SetupMessage(BaseModel):
    type: Literal["setup"]
    callSid: str
    from_: str | None = None  # populated via alias below
    to: str | None = None
    sessionId: str | None = None

    model_config = {"populate_by_name": True}


class PromptMessage(BaseModel):
    type: Literal["prompt"]
    voicePrompt: str
    lang: str | None = None
    last: bool = True


class DTMFMessage(BaseModel):
    type: Literal["dtmf"]
    digit: str


class InterruptMessage(BaseModel):
    type: Literal["interrupt"]


class ErrorMessage(BaseModel):
    type: Literal["error"]
    description: str | None = None
    callSid: str | None = None


# ── Outbound messages TO Twilio ConversationRelay ───────────────────────────

class TextMessage(BaseModel):
    type: Literal["text"] = "text"
    token: str
    last: bool = False
    lang: str | None = None
    interruptible: bool = True
    preemptible: bool = False


class PlayMessage(BaseModel):
    type: Literal["play"] = "play"
    source: str
    loop: int = 1
    interruptible: bool = True
    preemptible: bool = False


class SendDigitsMessage(BaseModel):
    type: Literal["sendDigits"] = "sendDigits"
    digits: str


class LanguageMessage(BaseModel):
    type: Literal["language"] = "language"
    ttsLanguage: str
    transcriptionLanguage: str


class EndSessionMessage(BaseModel):
    type: Literal["end"] = "end"
    handoffData: str | None = None  # JSON-encoded string


def parse_inbound(data: dict[str, Any]) -> SetupMessage | PromptMessage | DTMFMessage | InterruptMessage | ErrorMessage | None:
    t = data.get("type")
    match t:
        case "setup":
            # Twilio sends "from" which is a Python keyword
            if "from" in data:
                data = {**data, "from_": data.pop("from")}
            return SetupMessage(**data)
        case "prompt":
            return PromptMessage(**data)
        case "dtmf":
            return DTMFMessage(**data)
        case "interrupt":
            return InterruptMessage(**data)
        case "error":
            return ErrorMessage(**data)
        case _:
            return None
