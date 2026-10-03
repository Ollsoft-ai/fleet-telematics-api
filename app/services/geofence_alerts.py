"""Geofence containment and enter/exit event detection."""

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Self

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Geofence,
    GeofenceEvent,
    GeofenceEventType,
    GeofenceKind,
    Machine,
    MachineGeofenceState,
)

logger = logging.getLogger(__name__)


def point_in_polygon(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    """Ray-casting test against an open ring of ``[lon, lat]`` vertices."""
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def apply_polygon(geofence: Geofence, ring: Sequence[tuple[float, float]]) -> None:
    """Set the polygon and its bounding box, used to skip most containment checks."""
    geofence.polygon = [[lon, lat] for lon, lat in ring]
    lons = [lon for lon, _ in ring]
    lats = [lat for _, lat in ring]
    geofence.min_longitude, geofence.max_longitude = min(lons), max(lons)
    geofence.min_latitude, geofence.max_latitude = min(lats), max(lats)


def contains(geofence: Geofence, lat: float, lon: float) -> bool:
    if not (
        geofence.min_latitude <= lat <= geofence.max_latitude
        and geofence.min_longitude <= lon <= geofence.max_longitude
    ):
        return False
    return point_in_polygon(lon, lat, geofence.polygon)


class GeofenceEvaluator:
    """Tracks one machine against its organization's active geofences during an ingest."""

    def __init__(
        self,
        session: AsyncSession,
        machine: Machine,
        geofences: Sequence[Geofence],
        states: dict[uuid.UUID, MachineGeofenceState],
    ) -> None:
        self._session = session
        self._machine = machine
        self._geofences = geofences
        self._states = states

    @classmethod
    async def load(cls, session: AsyncSession, machine: Machine) -> Self:
        geofences = await session.scalars(
            select(Geofence).where(
                Geofence.organization_id == machine.organization_id,
                Geofence.is_active.is_(True),
            )
        )
        states = await session.scalars(
            select(MachineGeofenceState).where(MachineGeofenceState.machine_id == machine.id)
        )
        return cls(session, machine, geofences.all(), {s.geofence_id: s for s in states})

    def evaluate(self, lat: float, lon: float, at: datetime) -> list[GeofenceEvent]:
        """Compare a position with the last known state; emit an event per transition."""
        events: list[GeofenceEvent] = []
        for geofence in self._geofences:
            inside = contains(geofence, lat, lon)
            state = self._states.get(geofence.id)
            if state is None:
                state = MachineGeofenceState(
                    machine_id=self._machine.id,
                    geofence_id=geofence.id,
                    inside=inside,
                    updated_at=at,
                )
                self._session.add(state)
                self._states[geofence.id] = state
                if inside:
                    events.append(self._emit(geofence, GeofenceEventType.ENTER, lat, lon, at))
                continue
            if at <= state.updated_at:
                continue
            state.updated_at = at
            if inside != state.inside:
                state.inside = inside
                event_type = GeofenceEventType.ENTER if inside else GeofenceEventType.EXIT
                events.append(self._emit(geofence, event_type, lat, lon, at))
        return events

    def _emit(
        self,
        geofence: Geofence,
        event_type: GeofenceEventType,
        lat: float,
        lon: float,
        at: datetime,
    ) -> GeofenceEvent:
        event = GeofenceEvent(
            geofence_id=geofence.id,
            machine_id=self._machine.id,
            event_type=event_type,
            occurred_at=at,
            latitude=lat,
            longitude=lon,
        )
        self._session.add(event)
        if geofence.kind is GeofenceKind.RESTRICTED and event_type is GeofenceEventType.ENTER:
            logger.warning(
                "Machine %s entered restricted geofence %s", self._machine.id, geofence.id
            )
        return event
