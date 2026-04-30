from __future__ import annotations
import logging
from typing import Any

from src.airtable.appointment_repository import appointment_repo
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class GetPatientAppointmentsTool(BaseTool):
    name = "get_patient_appointments"
    description = (
        "Get a patient's upcoming booked appointments. "
        "Call this before rescheduling or cancelling to get the real appointment_id."
    )

    parameters = {
        "type": "object",
        "properties": {
            "patient_id": {
                "type": "string",
                "description": "The Airtable record ID of the patient, from lookup_patient.",
            },
        },
        "required": ["patient_id"],
    }

    async def run(self, input: dict[str, Any], state: SessionState) -> ToolResult:
        patient_id = input.get("patient_id", "")
        logger.info("tool=%s patient=%s", self.name, patient_id)

        appointments = appointment_repo.get_by_patient(patient_id)
        if not appointments:
            return ToolResult(tool_name=self.name, success=True, data={"appointments": [], "message": "No upcoming appointments found for this patient."})

        data = [
            {
                "appointment_id": a.id,
                "provider_id": a.provider_id,
                "start": a.start_time.isoformat(),
                "end": a.end_time.isoformat(),
                "status": a.status,
            }
            for a in appointments
        ]
        logger.info("tool=%s found %d appointment(s) for patient=%s", self.name, len(data), patient_id)
        return ToolResult(tool_name=self.name, success=True, data={"appointments": data})
