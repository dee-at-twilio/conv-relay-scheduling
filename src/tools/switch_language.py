from __future__ import annotations
import logging
from typing import Any

from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)

_SUPPORTED = {"en-US", "es-US", "fr-FR", "de-DE"}


class SwitchLanguageTool(BaseTool):
    name = "switch_language"
    description = (
        "Switch the call's spoken and transcription language when the user asks to "
        "continue in another language. Only call this when the user explicitly requests it."
    )

    parameters = {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": sorted(_SUPPORTED),
                "description": "BCP-47 language code, e.g. 'es-US' for Spanish.",
            },
        },
        "required": ["language"],
    }

    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult:
        language = args.get("language", "")
        if language not in _SUPPORTED:
            return ToolResult(
                tool_name=self.name,
                success=False,
                error=f"Unsupported language: {language}",
            )
        if language == state.language:
            return ToolResult(
                tool_name=self.name,
                success=True,
                data={"language": language, "changed": False},
            )
        state.pending_language_switch = language
        logger.info("tool=%s callSid=%s language=%s", self.name, state.call_sid, language)
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={"language": language, "changed": True},
        )
