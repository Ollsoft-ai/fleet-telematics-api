"""Machine registry."""

import logging
import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AdminUser, CurrentUser, ManagerUser, issue_machine_api_key
from app.db import SessionDep, utcnow
from app.models import Machine, MachineType, User
from app.schemas.machine import (
    MachineApiKeyIssued,
    MachineCreate,
    MachineRead,
    MachineRegistered,
    MachineUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/machines", tags=["machines"])


async def get_org_machine(session: AsyncSession, user: User, machine_id: uuid.UUID) -> Machine:
    """Load a machine owned by the user's organization, or raise 404."""
    machine = await session.get(Machine, machine_id)
    if machine is None or machine.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")
    return machine


@router.get("", response_model=list[MachineRead])
async def list_machines(
    session: SessionDep,
    user: CurrentUser,
    machine_type: MachineType | None = None,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Machine]:
    stmt = select(Machine).where(Machine.organization_id == user.organization_id)
    if machine_type is not None:
        stmt = stmt.where(Machine.machine_type == machine_type)
    if not include_inactive:
        stmt = stmt.where(Machine.is_active.is_(True))
    stmt = stmt.order_by(Machine.serial_number).limit(limit).offset(offset)
    return (await session.scalars(stmt)).all()


@router.post("", response_model=MachineRegistered, status_code=status.HTTP_201_CREATED)
async def register_machine(
    body: MachineCreate, session: SessionDep, user: AdminUser
) -> MachineRegistered:
    key = issue_machine_api_key()
    machine = Machine(
        organization_id=user.organization_id,
        api_key_prefix=key.prefix,
        api_key_hash=key.digest,
        **body.model_dump(),
    )
    session.add(machine)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A machine with this serial number already exists"
        ) from None
    await session.refresh(machine)
    logger.info("Machine %s registered by user %s", machine.id, user.id)
    return MachineRegistered(
        machine=MachineRead.model_validate(machine),
        credentials=MachineApiKeyIssued(api_key=key.token, rotated_at=machine.api_key_rotated_at),
    )


@router.get("/{machine_id}", response_model=MachineRead)
async def get_machine(machine_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> Machine:
    return await get_org_machine(session, user, machine_id)


@router.patch("/{machine_id}", response_model=MachineRead)
async def update_machine(
    machine_id: uuid.UUID, body: MachineUpdate, session: SessionDep, user: ManagerUser
) -> Machine:
    machine = await get_org_machine(session, user, machine_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(machine, field, value)
    await session.commit()
    return machine


@router.post("/{machine_id}/api-key", response_model=MachineApiKeyIssued)
async def rotate_api_key(
    machine_id: uuid.UUID, session: SessionDep, user: AdminUser
) -> MachineApiKeyIssued:
    machine = await get_org_machine(session, user, machine_id)
    key = issue_machine_api_key()
    machine.api_key_prefix = key.prefix
    machine.api_key_hash = key.digest
    machine.api_key_rotated_at = utcnow()
    await session.commit()
    logger.info("API key for machine %s rotated by user %s", machine.id, user.id)
    return MachineApiKeyIssued(api_key=key.token, rotated_at=machine.api_key_rotated_at)
