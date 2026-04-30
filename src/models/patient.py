from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


class PatientRecord(BaseModel):
    id: str
    name: str
    phone: str
    email: str | None = None


class AppointmentRecord(BaseModel):
    id: str
    patient_id: str
    provider_id: str
    start_time: datetime
    end_time: datetime
    status: str  # available | booked | cancelled | completed
    notes: str | None = None
