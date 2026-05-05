from __future__ import annotations
import logging
from datetime import datetime
from typing import Any

from src.airtable.appointment_repository import appointment_repo
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ScheduleAppointmentTool(BaseTool):
    name = "schedule_appointment"
    description = (
        "Book an appointment for a patient with a provider. "
        "Use the provider_id and a start/end time returned by check_availability. "
        "Requires patient_id from lookup_patient."
    )

    parameters = {
        "type": "object",
        "properties": {
            "provider_id": {
                "type": "string",
                "description": "The Airtable record ID of the provider, from check_availability.",
            },
            "start": {
                "type": "string",
                "description": "Appointment start time in ISO format, from check_availability.",
            },
            "end": {
                "type": "string",
                "description": "Appointment end time in ISO format, from check_availability.",
            },
        },
        "required": ["provider_id", "start", "end"],
    }

    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult:
        provider_id = args.get("provider_id", "")
        patient_id = state.patient_id or ""
        start = args.get("start", "")
        end = args.get("end", "")

        if not patient_id:
            return ToolResult(tool_name=self.name, success=False, error="Patient not identified. Please call lookup_patient first.")

        logger.info("tool=%s provider=%s patient=%s start=%s", self.name, provider_id, patient_id, start)
        try:
            appt = appointment_repo.create(
                provider_id=provider_id,
                patient_id=patient_id,
                start_time=datetime.fromisoformat(start),
                end_time=datetime.fromisoformat(end),
            )
        except Exception as e:
            logger.error("tool=%s failed error=%s", self.name, e)
            return ToolResult(tool_name=self.name, success=False, error="Could not book the appointment. Please try again.")

        logger.info("tool=%s booked appointment id=%s start=%s", self.name, appt.id, appt.start_time)
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "appointment_id": appt.id,
                "start": appt.start_time.isoformat(),
                "end": appt.end_time.isoformat(),
                "status": appt.status,
            },
        )
