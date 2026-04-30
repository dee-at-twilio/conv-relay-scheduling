from __future__ import annotations
import logging
from datetime import datetime, date

from src.airtable.client import airtable_client
from src.models.patient import AppointmentRecord

logger = logging.getLogger(__name__)

_TABLE = "Appointments"


def _to_appointment(record: dict) -> AppointmentRecord:
    f = record["fields"]
    # Provider and Patient are linked-record fields — Airtable returns a list of record IDs
    provider_ids: list[str] = f.get("Provider", [])
    patient_ids: list[str] = f.get("Patient", [])
    return AppointmentRecord(
        id=record["id"],
        patient_id=patient_ids[0] if patient_ids else "",
        provider_id=provider_ids[0] if provider_ids else "",
        start_time=datetime.fromisoformat(f["Start Time"]),
        end_time=datetime.fromisoformat(f["End Time"]),
        status=f.get("Status", "available"),
        notes=f.get("Notes"),
    )


class AppointmentRepository:
    def get_available_slots(
        self,
        provider_id: str,
        from_date: date,
        to_date: date,
    ) -> list[AppointmentRecord]:
        formula = (
            f"AND("
            f"{{Status}}='available',"
            f"FIND('{provider_id}',ARRAYJOIN({{Provider}})),"
            f"IS_AFTER({{Start Time}},'{from_date.isoformat()}'),"
            f"IS_BEFORE({{Start Time}},'{to_date.isoformat()}')"
            f")"
        )
        try:
            records = airtable_client.get_all(_TABLE, formula)
            return [_to_appointment(r) for r in records]
        except Exception:
            logger.exception("get_available_slots failed provider=%s", provider_id)
            return []

    def get_booked_for_provider(self, provider_id: str, from_date: date, to_date: date) -> list[AppointmentRecord]:
        formula = (
            f"AND("
            f"{{Status}}='booked',"
            f"FIND('{provider_id}',ARRAYJOIN({{Provider}})),"
            f"IS_AFTER({{Start Time}},'{from_date.isoformat()}'),"
            f"IS_BEFORE({{Start Time}},'{to_date.isoformat()}')"
            f")"
        )
        try:
            records = airtable_client.get_all(_TABLE, formula)
            return [_to_appointment(r) for r in records]
        except Exception:
            logger.exception("get_booked_for_provider failed provider=%s", provider_id)
            return []

    def get_by_patient(self, patient_id: str) -> list[AppointmentRecord]:
        formula = (
            f"AND("
            f"{{Status}}='booked',"
            f"FIND('{patient_id}',ARRAYJOIN({{Patient}}))"
            f")"
        )
        try:
            records = airtable_client.get_all(_TABLE, formula)
            return [_to_appointment(r) for r in records]
        except Exception:
            logger.exception("get_by_patient failed patient=%s", patient_id)
            return []

    def book_slot(self, slot_id: str, patient_id: str) -> AppointmentRecord:
        """Link a patient to a pre-existing available slot and mark it booked."""
        try:
            record = airtable_client.update_record(_TABLE, slot_id, {
                "Patient": [patient_id],
                "Status": "booked",
            })
            return _to_appointment(record)
        except Exception:
            logger.exception("book_slot failed slot=%s patient=%s", slot_id, patient_id)
            raise

    def cancel(self, appointment_id: str) -> AppointmentRecord:
        try:
            record = airtable_client.update_record(_TABLE, appointment_id, {"Status": "cancelled"})
            return _to_appointment(record)
        except Exception:
            logger.exception("cancel failed appointment=%s", appointment_id)
            raise


appointment_repo = AppointmentRepository()
