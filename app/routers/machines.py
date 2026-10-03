"""Machine registry."""

import logging
import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    AdminUser,
    CurrentUser,
    ManagerUser,
    create_location_share_token,
    decode_location_share_token,
    issue_machine_api_key,
)
from app.config import SettingsDep
from app.db import SessionDep, utcnow
from app.models import Machine, MachineType, User
from app.schemas.machine import (
    MachineApiKeyIssued,
    MachineCreate,
    MachineLocationShare,
    MachineRead,
    MachineRegistered,
    MachineUpdate,
    SharedMachineLocation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/machines", tags=["machines"])
share_router = APIRouter(tags=["machines"])

SHARED_LOCATION_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shared machine location</title></head>
<body>
<h1>Machine location</h1><p id="location" role="status">Loading location…</p>
<script>
const key = new URLSearchParams(window.location.hash.slice(1)).get("token");
async function refreshLocation() {
  if (!key) {
    document.getElementById("location").textContent = "Invalid share link.";
    return;
  }
  try {
    const response = await fetch("/shared/machines/location/data", {
      headers: {"X-Location-Share-Key": key}, cache: "no-store"
    });
    if (!response.ok) {
      document.getElementById("location").textContent =
        response.status === 404 ? "Share link expired or unavailable." : "Location unavailable.";
      return;
    }
    const location = await response.json();
    document.getElementById("location").textContent =
      location.latitude === null || location.longitude === null
        ? "No position reported yet."
        : `Latitude: ${location.latitude}, longitude: ${location.longitude}` +
          (location.last_seen_at ? ` (last seen: ${location.last_seen_at})` : "");
  } catch (_) {
    document.getElementById("location").textContent = "Location unavailable.";
  }
}
refreshLocation();
setInterval(refreshLocation, 30000);
</script>
</body></html>"""


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


@router.post(
    "/{machine_id}/location-share",
    response_model=MachineLocationShare,
    status_code=status.HTTP_201_CREATED,
)
async def create_location_share(
    machine_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    user: ManagerUser,
    settings: SettingsDep,
) -> MachineLocationShare:
    machine = await get_org_machine(session, user, machine_id)
    if not machine.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")
    token, expires_at = create_location_share_token(machine, settings)
    response.headers["Cache-Control"] = "no-store"
    return MachineLocationShare(
        url=f"{request.url_for('view_shared_machine_location')}#token={token}",
        expires_at=expires_at,
    )


@share_router.get("/shared/machines/location", response_class=HTMLResponse)
async def view_shared_machine_location() -> HTMLResponse:
    return HTMLResponse(
        SHARED_LOCATION_PAGE,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@share_router.get("/shared/machines/location/data", response_model=SharedMachineLocation)
async def get_shared_machine_location(
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    share_key: Annotated[str | None, Header(alias="X-Location-Share-Key")] = None,
) -> SharedMachineLocation:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if share_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share link not found or expired")
    machine_id, organization_id = decode_location_share_token(share_key, settings)
    machine = await session.get(Machine, machine_id)
    if machine is None or not machine.is_active or machine.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share link not found or expired")
    return SharedMachineLocation(
        latitude=machine.last_latitude,
        longitude=machine.last_longitude,
        last_seen_at=machine.last_seen_at,
    )
