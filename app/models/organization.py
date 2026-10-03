"""Customer organizations: contractors and rental companies."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, str_enum, utcnow


class OrganizationKind(StrEnum):
    CONTRACTOR = "contractor"
    RENTAL = "rental"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[OrganizationKind] = mapped_column(str_enum(OrganizationKind))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
