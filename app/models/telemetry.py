"""Telemetry samples and J1939 diagnostic trouble codes."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Float, ForeignKey, Integer, SmallInteger, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, BigIntPK, UTCDateTime, str_enum, utcnow


class TelemetryRecord(Base):
    __tablename__ = "telemetry_records"
    __table_args__ = (UniqueConstraint("machine_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    speed_kmh: Mapped[float | None] = mapped_column(Float)
    engine_hours: Mapped[float | None] = mapped_column(Float)
    fuel_level_pct: Mapped[float | None] = mapped_column(Float)
    hydraulic_pressure_bar: Mapped[float | None] = mapped_column(Float)
    coolant_temp_c: Mapped[float | None] = mapped_column(Float)


class FaultSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class FaultCode(Base):
    """A diagnostic trouble code (SPN/FMI pair) as last reported by a machine."""

    __tablename__ = "fault_codes"
    __table_args__ = (UniqueConstraint("machine_id", "spn", "fmi"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"))
    spn: Mapped[int] = mapped_column(Integer)
    fmi: Mapped[int] = mapped_column(SmallInteger)
    source_address: Mapped[int | None] = mapped_column(SmallInteger)
    severity: Mapped[FaultSeverity] = mapped_column(str_enum(FaultSeverity))
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
