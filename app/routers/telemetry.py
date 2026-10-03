"""Telemetry ingest from machines and telemetry queries for the portal."""

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from pydantic import AwareDatetime
from sqlalchemy import select

from app.auth import CurrentMachine, CurrentUser
from app.db import SessionDep, get_sessionmaker, utcnow
from app.models import FaultCode, TelemetryRecord
from app.routers.machines import get_org_machine
from app.schemas.telemetry import FaultCodeRead, IngestResult, TelemetryBatch, TelemetryRead
from app.services.dealer_sync import flush_pending
from app.services.ingest import ingest_batch

router = APIRouter(tags=["telemetry"])


@router.post("/telemetry", response_model=IngestResult, status_code=status.HTTP_202_ACCEPTED)
async def ingest_telemetry(
    batch: TelemetryBatch,
    request: Request,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    machine: CurrentMachine,
) -> IngestResult:
    outcome = await ingest_batch(session, machine, batch)
    await session.commit()

    dealer_client = getattr(request.app.state, "dealer_client", None)
    if outcome.service_events and dealer_client is not None:
        background_tasks.add_task(flush_pending, get_sessionmaker(), dealer_client)

    return IngestResult(
        accepted=outcome.accepted, duplicates=outcome.duplicates, rejected=outcome.rejected
    )


@router.get("/machines/{machine_id}/telemetry", response_model=list[TelemetryRead])
async def get_machine_telemetry(
    machine_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> Sequence[TelemetryRecord]:
    machine = await get_org_machine(session, user, machine_id)
    end = end or utcnow()
    start = start or end - timedelta(hours=24)
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start must be before end")

    stmt = (
        select(TelemetryRecord)
        .where(
            TelemetryRecord.machine_id == machine.id,
            TelemetryRecord.recorded_at >= start,
            TelemetryRecord.recorded_at < end,
        )
        .order_by(TelemetryRecord.recorded_at.desc())
        .limit(limit)
    )
    return (await session.scalars(stmt)).all()


@router.get("/machines/{machine_id}/faults", response_model=list[FaultCodeRead])
async def get_machine_faults(
    machine_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser,
    active_only: bool = True,
) -> Sequence[FaultCode]:
    machine = await get_org_machine(session, user, machine_id)
    stmt = select(FaultCode).where(FaultCode.machine_id == machine.id)
    if active_only:
        stmt = stmt.where(FaultCode.is_active.is_(True))
    stmt = stmt.order_by(FaultCode.last_seen_at.desc())
    return (await session.scalars(stmt)).all()
