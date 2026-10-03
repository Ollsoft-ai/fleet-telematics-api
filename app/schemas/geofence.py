import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.models import GeofenceEventType, GeofenceKind
from app.schemas.common import ORMModel

MAX_VERTICES = 500

Longitude = Annotated[float, Field(ge=-180, le=180)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Vertex = tuple[Longitude, Latitude]


def _ring_area(ring: list[Vertex]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True)
    )


class GeofenceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: GeofenceKind = GeofenceKind.SITE
    polygon: list[Vertex] = Field(
        min_length=3,
        max_length=MAX_VERTICES + 1,
        description="Outer ring as [longitude, latitude] pairs. A closing vertex is optional.",
    )

    @field_validator("polygon")
    @classmethod
    def _valid_ring(cls, ring: list[Vertex]) -> list[Vertex]:
        if len(ring) > 3 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if len(ring) < 3:
            raise ValueError("polygon needs at least 3 distinct vertices")
        if len(ring) > MAX_VERTICES:
            raise ValueError(f"polygon may have at most {MAX_VERTICES} vertices")
        if len(set(ring)) != len(ring):
            raise ValueError("polygon contains duplicate vertices")
        if max(lon for lon, _ in ring) - min(lon for lon, _ in ring) > 180:
            raise ValueError("polygons crossing the antimeridian are not supported")
        if abs(_ring_area(ring)) < 1e-10:
            raise ValueError("polygon has no area")
        return ring


class GeofenceRead(ORMModel):
    id: uuid.UUID
    name: str
    kind: GeofenceKind
    polygon: list[tuple[float, float]]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class GeofenceEventRead(ORMModel):
    id: int
    geofence_id: uuid.UUID
    machine_id: uuid.UUID
    event_type: GeofenceEventType
    occurred_at: datetime
    latitude: float
    longitude: float
