import uuid
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.models import FaultSeverity
from app.schemas.common import ORMModel

MAX_SAMPLES_PER_BATCH = 1000


class LampStatus(StrEnum):
    NONE = "none"
    MALFUNCTION = "malfunction"
    PROTECT = "protect"
    AMBER_WARNING = "amber_warning"
    RED_STOP = "red_stop"


class FaultCodeIn(BaseModel):
    spn: int = Field(ge=0, le=524_287)
    fmi: int = Field(ge=0, le=31)
    source_address: int | None = Field(default=None, ge=0, le=255)
    lamp: LampStatus = LampStatus.NONE
    occurrence_count: int = Field(default=1, ge=1, le=126)


class TelemetrySample(BaseModel):
    recorded_at: AwareDatetime
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    speed_kmh: float | None = Field(default=None, ge=0, le=120)
    engine_hours: float | None = Field(default=None, ge=0, le=500_000)
    fuel_level_pct: float | None = Field(default=None, ge=0, le=100)
    hydraulic_pressure_bar: float | None = Field(default=None, ge=0, le=700)
    coolant_temp_c: float | None = Field(default=None, ge=-60, le=150)
    fault_codes: list[FaultCodeIn] | None = Field(
        default=None,
        max_length=64,
        description="Active DTCs (DM1). Omit if not read; an empty list clears all faults.",
    )

    @model_validator(mode="after")
    def _position_is_complete(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be sent together")
        return self


class TelemetryBatch(BaseModel):
    samples: list[TelemetrySample] = Field(min_length=1, max_length=MAX_SAMPLES_PER_BATCH)


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: int


class TelemetryRead(ORMModel):
    recorded_at: datetime
    latitude: float | None
    longitude: float | None
    speed_kmh: float | None
    engine_hours: float | None
    fuel_level_pct: float | None
    hydraulic_pressure_bar: float | None
    coolant_temp_c: float | None


class FaultCodeRead(ORMModel):
    id: uuid.UUID
    spn: int
    fmi: int
    source_address: int | None
    severity: FaultSeverity
    occurrence_count: int
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None
