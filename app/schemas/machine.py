import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import MachineType
from app.schemas.common import ORMModel


class MachineCreate(BaseModel):
    serial_number: str = Field(min_length=3, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9-]*$")
    manufacturer: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    machine_type: MachineType
    year_of_manufacture: int | None = Field(default=None, ge=1950, le=2100)


class MachineUpdate(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=100)
    year_of_manufacture: int | None = Field(default=None, ge=1950, le=2100)
    is_active: bool | None = None


class MachineRead(ORMModel):
    id: uuid.UUID
    serial_number: str
    manufacturer: str
    model: str
    machine_type: MachineType
    year_of_manufacture: int | None
    is_active: bool
    engine_hours: float
    fuel_level_pct: float | None
    last_latitude: float | None
    last_longitude: float | None
    last_seen_at: datetime | None


class MachineApiKeyIssued(BaseModel):
    api_key: str = Field(description="Shown once. Store it on the machine terminal.")
    rotated_at: datetime


class MachineRegistered(BaseModel):
    machine: MachineRead
    credentials: MachineApiKeyIssued


class MachineLocationShare(BaseModel):
    url: str
    expires_at: datetime


class SharedMachineLocation(BaseModel):
    latitude: float | None
    longitude: float | None
    last_seen_at: datetime | None
