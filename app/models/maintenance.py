"""Service events raised for the dealer network."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, JSONType, UTCDateTime, str_enum, utcnow


class ServiceEventKind(StrEnum):
    SCHEDULED_SERVICE = "scheduled_service"
    FAULT = "fault"


class ServiceEvent(Base):
    __tablename__ = "service_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    machine_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), index=True
    )
    fault_code_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fault_codes.id", ondelete="SET NULL")
    )
    kind: Mapped[ServiceEventKind] = mapped_column(str_enum(ServiceEventKind))
    title: Mapped[str] = mapped_column(String(200))
    engine_hours: Mapped[float] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    dealer_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    dealer_sync_attempts: Mapped[int] = mapped_column(Integer, default=0)
    dealer_last_error: Mapped[str | None] = mapped_column(String(500))
