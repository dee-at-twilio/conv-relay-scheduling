from __future__ import annotations
import logging
from typing import Any

from src.airtable.appointment_repository import appointment_repo
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class CancelAppointmentTool(BaseTool):
    name = "cancel_appointment"
    description = "Cancel an existing booked appointment. You must call get_patient_appointments first to get the real appointment_id — never guess it."

    parameters = {
        "type": "object",
        "properties": {
            "appointment_id": {
                "type": "string",
                "description": "The Airtable record ID of the booked appointment to cancel.",
            },
        },
        "required": ["appointment_id"],
    }

    async def run(self, input: dict[str, Any], state: SessionState) -> ToolResult:
        appointment_id = input.get("appointment_id", "")

        logger.info("tool=%s cancelling appointment=%s", self.name, appointment_id)
        try:
            appt = appointment_repo.cancel(appointment_id)
        except Exception as e:
            logger.error("tool=%s failed id=%s error=%s", self.name, appointment_id, e)
            return ToolResult(tool_name=self.name, success=False, error="Could not cancel the appointment.")

        logger.info("tool=%s cancelled appointment id=%s", self.name, appt.id)
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={"appointment_id": appt.id, "status": appt.status},
        )
