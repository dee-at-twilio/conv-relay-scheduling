from __future__ import annotations
import logging
from datetime import datetime
from typing import Any

from src.airtable.appointment_repository import appointment_repo
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class RescheduleAppointmentTool(BaseTool):
    name = "reschedule_appointment"
    description = (
        "Reschedule an existing booked appointment to a new time. "
        "Cancels the old appointment and creates a new one. "
        "You must call get_patient_appointments first to get the real appointment_id — never guess it."
    )

    parameters = {
        "type": "object",
        "properties": {
            "appointment_id": {
                "type": "string",
                "description": "The Airtable record ID of the existing booked appointment to cancel.",
            },
            "provider_id": {
                "type": "string",
                "description": "The Airtable record ID of the provider.",
            },
            "start": {
                "type": "string",
                "description": "New appointment start time in ISO format, from check_availability.",
            },
            "end": {
                "type": "string",
                "description": "New appointment end time in ISO format, from check_availability.",
            },
        },
        "required": ["appointment_id", "provider_id", "start", "end"],
    }

    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult:
        appointment_id = args.get("appointment_id", "")
        provider_id = args.get("provider_id", "")
        patient_id = state.patient_id or ""
        start = args.get("start", "")
        end = args.get("end", "")

        if not patient_id:
            return ToolResult(tool_name=self.name, success=False, error="Patient not identified. Please call lookup_patient first.")

        logger.info("tool=%s cancel old=%s patient=%s new start=%s", self.name, appointment_id, patient_id, start)
        try:
            appointment_repo.cancel(appointment_id)
        except Exception as e:
            logger.error("tool=%s cancel failed id=%s error=%s", self.name, appointment_id, e)
            return ToolResult(tool_name=self.name, success=False, error="Could not cancel the existing appointment.")

        try:
            new_appt = appointment_repo.create(
                provider_id=provider_id,
                patient_id=patient_id,
                start_time=datetime.fromisoformat(start),
                end_time=datetime.fromisoformat(end),
            )
        except Exception as e:
            logger.error("tool=%s rebook failed error=%s", self.name, e)
            return ToolResult(tool_name=self.name, success=False, error="Cancelled the old appointment but could not book the new time. Please call back to rebook.")

        logger.info("tool=%s rescheduled to id=%s start=%s", self.name, new_appt.id, new_appt.start_time)
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "appointment_id": new_appt.id,
                "start": new_appt.start_time.isoformat(),
                "end": new_appt.end_time.isoformat(),
                "status": new_appt.status,
            },
        )
