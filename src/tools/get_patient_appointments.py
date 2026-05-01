from __future__ import annotations
import logging
from typing import Any

from src.airtable.appointment_repository import appointment_repo
from src.airtable.patient_repository import patient_repo
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class GetPatientAppointmentsTool(BaseTool):
    name = "get_patient_appointments"
    description = (
        "Get the calling patient's upcoming booked appointments. "
        "Call this before cancelling or rescheduling — it auto-identifies the patient by their phone number. "
        "Returns appointment_ids and times. If multiple, ask the patient which one before proceeding."
    )

    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult:
        patient = patient_repo.find_by_phone(state.from_number)
        if not patient:
            return ToolResult(tool_name=self.name, success=False, error="Could not find a patient record for this phone number.")

        logger.info("tool=%s patient=%s", self.name, patient.id)
        appointments = appointment_repo.get_by_patient(patient.id)
        if not appointments:
            return ToolResult(tool_name=self.name, success=True, data={"appointments": [], "message": "No upcoming appointments found."})

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
        logger.info("tool=%s found %d appointment(s) for patient=%s", self.name, len(data), patient.id)
        return ToolResult(tool_name=self.name, success=True, data={"appointments": data})
