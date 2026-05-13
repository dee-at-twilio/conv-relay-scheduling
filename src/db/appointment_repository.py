from __future__ import annotations
import logging
from datetime import datetime, date

from src.db.database import db
from src.models.patient import AppointmentRecord

logger = logging.getLogger(__name__)


def _to_appointment(row) -> AppointmentRecord:
    return AppointmentRecord(
        id=row["id"],
        patient_id=row["patient_id"] or "",
        provider_id=row["provider_id"],
        start_time=datetime.fromisoformat(row["start_time"]),
        end_time=datetime.fromisoformat(row["end_time"]),
        status=row["status"],
        notes=row["notes"],
    )


class AppointmentRepository:
    def get_booked_for_provider(self, provider_id: str, from_date: date, to_date: date) -> list[AppointmentRecord]:
        try:
            rows = db.fetchall(
                "SELECT * FROM appointments WHERE provider_id = ? AND status = 'booked' "
                "AND start_time >= ? AND start_time <= ?",
                (provider_id, from_date.isoformat(), to_date.isoformat()),
            )
            return [_to_appointment(r) for r in rows]
        except Exception:
            logger.exception("get_booked_for_provider failed provider=%s", provider_id)
            return []

    def get_by_patient(self, patient_name: str) -> list[AppointmentRecord]:
        try:
            rows = db.fetchall(
                "SELECT a.* FROM appointments a "
                "JOIN patients p ON a.patient_id = p.id "
                "WHERE a.status = 'booked' AND p.name LIKE ?",
                (f"%{patient_name}%",),
            )
            return [_to_appointment(r) for r in rows]
        except Exception:
            logger.exception("get_by_patient failed patient=%s", patient_name)
            return []

    def get_all(self) -> list[dict]:
        try:
            rows = db.fetchall(
                "SELECT a.*, p.name AS patient_name, pr.name AS provider_name "
                "FROM appointments a "
                "LEFT JOIN patients p ON a.patient_id = p.id "
                "LEFT JOIN providers pr ON a.provider_id = pr.id "
                "ORDER BY a.start_time DESC"
            )
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("get_all appointments failed")
            return []

    def get_provider_name(self, provider_id: str) -> str:
        try:
            row = db.fetchone("SELECT name FROM providers WHERE id = ?", (provider_id,))
            return row["name"] if row else ""
        except Exception:
            logger.exception("get_provider_name failed provider=%s", provider_id)
            return ""

    def get_all_providers(self) -> list[dict]:
        try:
            rows = db.fetchall("SELECT * FROM providers ORDER BY name")
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("get_all_providers failed")
            return []

    def create_provider(self, name: str, specialty: str | None = None) -> dict:
        pid = db.new_id()
        db.execute("INSERT INTO providers (id, name, specialty) VALUES (?, ?, ?)", (pid, name, specialty))
        return {"id": pid, "name": name, "specialty": specialty}

    def update_provider(self, provider_id: str, name: str, specialty: str | None = None) -> None:
        db.execute("UPDATE providers SET name = ?, specialty = ? WHERE id = ?", (name, specialty, provider_id))

    def delete_provider(self, provider_id: str) -> None:
        db.execute("DELETE FROM providers WHERE id = ?", (provider_id,))

    def create(
        self,
        provider_id: str,
        patient_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> AppointmentRecord:
        aid = db.new_id()
        try:
            db.execute(
                "INSERT INTO appointments (id, provider_id, patient_id, start_time, end_time, status) "
                "VALUES (?, ?, ?, ?, ?, 'booked')",
                (aid, provider_id, patient_id, start_time.isoformat(), end_time.isoformat()),
            )
            return AppointmentRecord(
                id=aid,
                provider_id=provider_id,
                patient_id=patient_id,
                start_time=start_time,
                end_time=end_time,
                status="booked",
            )
        except Exception:
            logger.exception("create appointment failed provider=%s patient=%s", provider_id, patient_id)
            raise

    def cancel(self, appointment_id: str) -> AppointmentRecord:
        try:
            db.execute(
                "UPDATE appointments SET status = 'cancelled' WHERE id = ?",
                (appointment_id,),
            )
            row = db.fetchone("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
            return _to_appointment(row)
        except Exception:
            logger.exception("cancel failed appointment=%s", appointment_id)
            raise

    def update(self, appointment_id: str, fields: dict) -> AppointmentRecord:
        allowed = {"status", "notes", "start_time", "end_time", "provider_id", "patient_id"}
        for key, val in fields.items():
            if key in allowed:
                db.execute(
                    f"UPDATE appointments SET {key} = ? WHERE id = ?",
                    (val, appointment_id),
                )
        row = db.fetchone("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
        return _to_appointment(row)


appointment_repo = AppointmentRepository()
