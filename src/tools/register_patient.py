from __future__ import annotations
import logging
from typing import Any

from src.airtable.patient_repository import patient_repo
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class RegisterPatientTool(BaseTool):
    name = "register_patient"
    description = "Create a new patient record using the caller's phone number and their confirmed name. Call this after lookup_patient returns 'Patient not found'."

    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Patient's full name, confirmed with the caller.",
            },
        },
        "required": ["name"],
    }

    async def run(self, args: dict[str, Any], state: SessionState) -> ToolResult:
        name = args.get("name", "")
        phone = state.from_number

        if not phone:
            return ToolResult(tool_name=self.name, success=False, error="No caller phone number available.")

        logger.info("tool=%s creating patient name=%s phone=%s", self.name, name, phone)
        try:
            patient = patient_repo.create(name=name, phone=phone)
        except Exception as e:
            logger.error("tool=%s failed error=%s", self.name, e)
            return ToolResult(tool_name=self.name, success=False, error="Could not create patient record. Please try again.")

        state.patient_id = patient.id
        state.patient_name = patient.name
        logger.info("tool=%s created patient id=%s name=%s", self.name, patient.id, patient.name)
        return ToolResult(tool_name=self.name, success=True, data=patient.model_dump())
