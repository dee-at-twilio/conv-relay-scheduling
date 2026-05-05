from __future__ import annotations
import logging
from typing import Any

from twilio.rest import Client

from src.config import config
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class SendSmsTool(BaseTool):
    name = "send_sms"
    description = (
        "Send an SMS confirmation to the patient. "
        "Call this after every successful booking, cancellation, or reschedule. "
        "Use the patient's phone number from their record and write a short confirmation message."
    )

    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Patient's phone number in E.164 format, e.g. +447876762080.",
            },
            "message": {
                "type": "string",
                "description": "The SMS text to send. Keep it concise — one or two sentences.",
            },
        },
        "required": ["to", "message"],
    }

    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult:
        to = args.get("to", "")
        message = args.get("message", "")

        logger.info("tool=%s to=%s", self.name, to)
        try:
            client = Client(config.twilio_account_sid, config.twilio_auth_token)
            sms = client.messages.create(
                to=to,
                from_=config.twilio_phone_number,
                body=message,
            )
        except Exception as e:
            logger.error("tool=%s failed to=%s error=%s", self.name, to, e)
            return ToolResult(tool_name=self.name, success=False, error="Could not send SMS confirmation.")

        logger.info("tool=%s sent sid=%s to=%s", self.name, sms.sid, to)
        return ToolResult(tool_name=self.name, success=True, data={"sid": sms.sid})
