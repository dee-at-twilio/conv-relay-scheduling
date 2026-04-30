from __future__ import annotations
import logging

from src.airtable.client import airtable_client
from src.models.patient import PatientRecord

logger = logging.getLogger(__name__)

_TABLE = "Patients"


def _to_patient(record: dict) -> PatientRecord:
    f = record["fields"]
    return PatientRecord(
        id=record["id"],
        name=f.get("Name", ""),
        phone=f.get("Phone", ""),
        email=f.get("Email"),
    )


class PatientRepository:
    def find_by_phone(self, phone: str) -> PatientRecord | None:
        try:
            records = airtable_client.get_all(_TABLE, f"{{Phone}}='{phone}'")
            return _to_patient(records[0]) if records else None
        except Exception:
            logger.exception("find_by_phone failed phone=%s", phone)
            return None

    def find_by_name(self, name: str) -> PatientRecord | None:
        try:
            records = airtable_client.get_all(_TABLE, f"{{Name}}='{name}'")
            return _to_patient(records[0]) if records else None
        except Exception:
            logger.exception("find_by_name failed name=%s", name)
            return None

    def create(self, name: str, phone: str, email: str | None = None) -> PatientRecord:
        fields: dict = {"Name": name, "Phone": phone}
        if email:
            fields["Email"] = email
        try:
            record = airtable_client.create_record(_TABLE, fields)
            return _to_patient(record)
        except Exception:
            logger.exception("create patient failed")
            raise


patient_repo = PatientRepository()
