"""Geofences and their enter/exit history."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, ManagerUser
from app.db import SessionDep
from app.models import Geofence, GeofenceEvent, MachineGeofenceState, User
from app.schemas.geofence import GeofenceEventRead, GeofenceRead, GeofenceWrite
from app.services.geofence_alerts import apply_polygon

router = APIRouter(prefix="/geofences", tags=["geofences"])


async def _get_org_geofence(session: AsyncSession, user: User, geofence_id: uuid.UUID) -> Geofence:
    geofence = await session.get(Geofence, geofence_id)
    if geofence is None or geofence.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Geofence not found")
    return geofence


@router.get("", response_model=list[GeofenceRead])
async def list_geofences(
    session: SessionDep, user: CurrentUser, include_archived: bool = False
) -> Sequence[Geofence]:
    stmt = select(Geofence).where(Geofence.organization_id == user.organization_id)
    if not include_archived:
        stmt = stmt.where(Geofence.is_active.is_(True))
    return (await session.scalars(stmt.order_by(Geofence.name))).all()


@router.post("", response_model=GeofenceRead, status_code=status.HTTP_201_CREATED)
async def create_geofence(body: GeofenceWrite, session: SessionDep, user: ManagerUser) -> Geofence:
    geofence = Geofence(organization_id=user.organization_id, name=body.name, kind=body.kind)
    apply_polygon(geofence, body.polygon)
    session.add(geofence)
    await session.commit()
    await session.refresh(geofence)
    return geofence


@router.get("/{geofence_id}", response_model=GeofenceRead)
async def get_geofence(geofence_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> Geofence:
    return await _get_org_geofence(session, user, geofence_id)


@router.put("/{geofence_id}", response_model=GeofenceRead)
async def replace_geofence(
    geofence_id: uuid.UUID, body: GeofenceWrite, session: SessionDep, user: ManagerUser
) -> Geofence:
    geofence = await _get_org_geofence(session, user, geofence_id)
    geofence.name = body.name
    geofence.kind = body.kind
    apply_polygon(geofence, body.polygon)
    # Shape changed: re-evaluate every machine from its next position.
    await session.execute(
        delete(MachineGeofenceState).where(MachineGeofenceState.geofence_id == geofence.id)
    )
    await session.commit()
    await session.refresh(geofence)
    return geofence


@router.delete("/{geofence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_geofence(
    geofence_id: uuid.UUID, session: SessionDep, user: ManagerUser
) -> Response:
    geofence = await _get_org_geofence(session, user, geofence_id)
    geofence.is_active = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{geofence_id}/events", response_model=list[GeofenceEventRead])
async def list_geofence_events(
    geofence_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    since: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> Sequence[GeofenceEvent]:
    geofence = await _get_org_geofence(session, user, geofence_id)
    stmt = select(GeofenceEvent).where(GeofenceEvent.geofence_id == geofence.id)
    if since is not None:
        stmt = stmt.where(GeofenceEvent.occurred_at >= since)
    stmt = stmt.order_by(GeofenceEvent.occurred_at, GeofenceEvent.id).limit(limit)
    return (await session.scalars(stmt)).all()
