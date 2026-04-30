from __future__ import annotations
import logging
from typing import Any

from src.airtable.patient_repository import patient_repo
from src.models.session import SessionState
from src.models.tools import ToolResult
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class PatientLookupTool(BaseTool):
    name = "lookup_patient"
    description = "Look up a patient record by phone number or name. Call this before booking or cancelling an appointment."

    parameters = {
        "type": "object",
        "properties": {
            "phone": {
                "type": "string",
                "description": "Patient phone number in E.164 format, e.g. +15551234567",
            },
            "name": {
                "type": "string",
                "description": "Patient full name, if phone is not available",
            },
        },
    }

    async def run(self, input: dict[str, Any], state: SessionState) -> ToolResult:
        phone = input.get("phone")
        name = input.get("name")

        if not phone and not name:
            return ToolResult(tool_name=self.name, success=False, error="Provide phone or name to look up a patient.")

        patient = None
        if phone:
            logger.info("tool=%s looking up phone=%s", self.name, phone)
            patient = patient_repo.find_by_phone(phone)
        if not patient and name:
            logger.info("tool=%s looking up name=%s", self.name, name)
            patient = patient_repo.find_by_name(name)

        if not patient:
            logger.info("tool=%s patient not found phone=%s name=%s", self.name, phone, name)
            return ToolResult(tool_name=self.name, success=False, error="Patient not found. Verify the name or phone number.")

        logger.info("tool=%s found patient id=%s name=%s", self.name, patient.id, patient.name)
        return ToolResult(tool_name=self.name, success=True, data=patient.model_dump())
