from __future__ import annotations
import logging

from src.db.database import db
from src.models.patient import PatientRecord

logger = logging.getLogger(__name__)


def _to_patient(row) -> PatientRecord:
    return PatientRecord(
        id=row["id"],
        name=row["name"],
        phone=row["phone"],
        email=row["email"],
    )


class PatientRepository:
    def find_by_phone(self, phone: str) -> PatientRecord | None:
        try:
            row = db.fetchone("SELECT * FROM patients WHERE phone = ?", (phone,))
            return _to_patient(row) if row else None
        except Exception:
            logger.exception("find_by_phone failed phone=%s", phone)
            return None

    def find_by_name(self, name: str) -> PatientRecord | None:
        try:
            row = db.fetchone("SELECT * FROM patients WHERE name LIKE ?", (f"%{name}%",))
            return _to_patient(row) if row else None
        except Exception:
            logger.exception("find_by_name failed name=%s", name)
            return None

    def get_all(self) -> list[PatientRecord]:
        try:
            rows = db.fetchall("SELECT * FROM patients ORDER BY name")
            return [_to_patient(r) for r in rows]
        except Exception:
            logger.exception("get_all patients failed")
            return []

    def create(self, name: str, phone: str, email: str | None = None) -> PatientRecord:
        pid = db.new_id()
        try:
            db.execute(
                "INSERT INTO patients (id, name, phone, email) VALUES (?, ?, ?, ?)",
                (pid, name, phone, email),
            )
            return PatientRecord(id=pid, name=name, phone=phone, email=email)
        except Exception:
            logger.exception("create patient failed name=%s phone=%s", name, phone)
            raise

    def update(self, patient_id: str, name: str | None = None, email: str | None = None) -> None:
        if name is not None:
            db.execute("UPDATE patients SET name = ? WHERE id = ?", (name, patient_id))
        if email is not None:
            db.execute("UPDATE patients SET email = ? WHERE id = ?", (email, patient_id))

    def delete(self, patient_id: str) -> None:
        db.execute("DELETE FROM patients WHERE id = ?", (patient_id,))


patient_repo = PatientRepository()
