import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

BADGE_PATTERN = r"^[A-Z0-9-]{3,32}$"
PIN_PATTERN = r"^\d{6}$"


class OperatorCreate(BaseModel):
    badge_number: str = Field(pattern=BADGE_PATTERN)
    full_name: str = Field(min_length=1, max_length=200)
    pin: str = Field(pattern=PIN_PATTERN)


class OperatorUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    pin: str | None = Field(default=None, pattern=PIN_PATTERN)


class OperatorRead(ORMModel):
    id: uuid.UUID
    badge_number: str
    full_name: str
    is_active: bool
    created_at: datetime


class TerminalSignIn(BaseModel):
    badge_number: str = Field(min_length=1, max_length=32)
    pin: str = Field(min_length=1, max_length=16)


class OperatorSessionRead(ORMModel):
    id: uuid.UUID
    operator_id: uuid.UUID
    machine_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None


class TerminalSession(OperatorSessionRead):
    operator_name: str
