"""Connected machines and their last known state."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Float, ForeignKey, SmallInteger, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, str_enum, utcnow


class MachineType(StrEnum):
    EXCAVATOR = "excavator"
    MINI_EXCAVATOR = "mini_excavator"
    WHEEL_LOADER = "wheel_loader"
    ROLLER = "roller"
    DOZER = "dozer"
    TELEHANDLER = "telehandler"


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    serial_number: Mapped[str] = mapped_column(String(64), unique=True)
    manufacturer: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    machine_type: Mapped[MachineType] = mapped_column(str_enum(MachineType))
    year_of_manufacture: Mapped[int | None] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    api_key_prefix: Mapped[str] = mapped_column(String(16), unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(64))
    api_key_rotated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    engine_hours: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_level_pct: Mapped[float | None] = mapped_column(Float)
    last_latitude: Mapped[float | None] = mapped_column(Float)
    last_longitude: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
