"""Public machine location and issued share link responses."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class MachineShareIssued(BaseModel):
    url: str
    expires_at: datetime


class SharedMachineLocation(BaseModel):
    machine_id: uuid.UUID
    latitude: float | None
    longitude: float | None
    last_seen_at: datetime | None
