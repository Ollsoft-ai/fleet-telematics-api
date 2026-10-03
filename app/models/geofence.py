"""Geofences (job sites, restricted areas) and the events they produce."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, BigIntPK, JSONType, UTCDateTime, str_enum, utcnow


class GeofenceKind(StrEnum):
    SITE = "site"
    RESTRICTED = "restricted"


class GeofenceEventType(StrEnum):
    ENTER = "enter"
    EXIT = "exit"


class Geofence(Base):
    """A polygon stored as an open ring of ``[longitude, latitude]`` pairs (GeoJSON order)."""

    __tablename__ = "geofences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[GeofenceKind] = mapped_column(str_enum(GeofenceKind), default=GeofenceKind.SITE)
    polygon: Mapped[list[list[float]]] = mapped_column(JSONType)
    min_latitude: Mapped[float] = mapped_column(Float)
    max_latitude: Mapped[float] = mapped_column(Float)
    min_longitude: Mapped[float] = mapped_column(Float)
    max_longitude: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class MachineGeofenceState(Base):
    """Whether a machine was inside a geofence at its last evaluated position."""

    __tablename__ = "machine_geofence_states"

    machine_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True
    )
    geofence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("geofences.id", ondelete="CASCADE"), primary_key=True
    )
    inside: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class GeofenceEvent(Base):
    __tablename__ = "geofence_events"
    __table_args__ = (Index("ix_geofence_events_geofence_occurred", "geofence_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    geofence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("geofences.id", ondelete="CASCADE"))
    machine_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("machines.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[GeofenceEventType] = mapped_column(str_enum(GeofenceEventType))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
