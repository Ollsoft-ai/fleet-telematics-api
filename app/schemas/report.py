import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models import MachineType


class UtilizationRow(BaseModel):
    machine_id: uuid.UUID
    serial_number: str
    machine_type: MachineType
    engine_hours_start: float | None
    engine_hours_end: float | None
    operating_hours: float
    samples: int
    last_seen_at: datetime | None


class UtilizationReport(BaseModel):
    period_start: datetime
    period_end: datetime
    total_operating_hours: float
    machines: list[UtilizationRow]


class FaultSummaryRow(BaseModel):
    machine_id: uuid.UUID
    serial_number: str
    critical: int = 0
    warning: int = 0
    info: int = 0
