from __future__ import annotations
from datetime import date, datetime
from pydantic import BaseModel


class PatientRecord(BaseModel):
    id: str
    name: str
    phone: str
    date_of_birth: date | None = None
    email: str | None = None


class AppointmentRecord(BaseModel):
    id: str
    patient_id: str
    provider: str
    start_time: datetime
    end_time: datetime
    status: str  # scheduled | cancelled | completed
    notes: str | None = None
