"""Fleet reports: utilization (JSON or CSV) and active fault summary."""

import csv
import io
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import AwareDatetime
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.db import SessionDep, utcnow
from app.models import FaultCode, FaultSeverity, Machine, MachineType, TelemetryRecord
from app.schemas.report import FaultSummaryRow, UtilizationReport, UtilizationRow

router = APIRouter(prefix="/reports", tags=["reports"])

MAX_PERIOD = timedelta(days=366)
DEFAULT_PERIOD = timedelta(days=7)
CSV_COLUMNS = (
    "machine_id",
    "serial_number",
    "machine_type",
    "engine_hours_start",
    "engine_hours_end",
    "operating_hours",
    "samples",
    "last_seen_at",
)


def _resolve_period(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or utcnow()
    start = start or end - DEFAULT_PERIOD
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "start must be before end")
    if end - start > MAX_PERIOD:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "period is limited to 366 days")
    return start, end


async def _utilization_rows(
    session: AsyncSession,
    organization_id: uuid.UUID,
    start: datetime,
    end: datetime,
    machine_type: MachineType | None,
) -> list[UtilizationRow]:
    in_period = and_(
        TelemetryRecord.machine_id == Machine.id,
        TelemetryRecord.recorded_at >= start,
        TelemetryRecord.recorded_at < end,
    )
    stmt = (
        select(
            Machine.id,
            Machine.serial_number,
            Machine.machine_type,
            func.min(TelemetryRecord.engine_hours).label("hours_start"),
            func.max(TelemetryRecord.engine_hours).label("hours_end"),
            func.count(TelemetryRecord.id).label("samples"),
            func.max(TelemetryRecord.recorded_at).label("last_seen_at"),
        )
        .outerjoin(TelemetryRecord, in_period)
        .where(Machine.organization_id == organization_id, Machine.is_active.is_(True))
        .group_by(Machine.id, Machine.serial_number, Machine.machine_type)
        .order_by(Machine.serial_number)
    )
    if machine_type is not None:
        stmt = stmt.where(Machine.machine_type == machine_type)

    rows = []
    for row in await session.execute(stmt):
        operating = 0.0
        if row.hours_start is not None and row.hours_end is not None:
            operating = round(max(row.hours_end - row.hours_start, 0.0), 1)
        rows.append(
            UtilizationRow(
                machine_id=row.id,
                serial_number=row.serial_number,
                machine_type=row.machine_type,
                engine_hours_start=row.hours_start,
                engine_hours_end=row.hours_end,
                operating_hours=operating,
                samples=row.samples,
                last_seen_at=row.last_seen_at,
            )
        )
    return rows


@router.get("/utilization", response_model=UtilizationReport)
async def utilization_report(
    session: SessionDep,
    user: CurrentUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    machine_type: MachineType | None = None,
) -> UtilizationReport:
    start, end = _resolve_period(start, end)
    rows = await _utilization_rows(session, user.organization_id, start, end, machine_type)
    return UtilizationReport(
        period_start=start,
        period_end=end,
        total_operating_hours=round(sum(r.operating_hours for r in rows), 1),
        machines=rows,
    )


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    # Keep spreadsheet apps from evaluating cell content as a formula.
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


@router.get("/utilization.csv", response_class=Response)
async def utilization_csv(
    session: SessionDep,
    user: CurrentUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    machine_type: MachineType | None = None,
) -> Response:
    start, end = _resolve_period(start, end)
    rows = await _utilization_rows(session, user.organization_id, start, end, machine_type)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        data = row.model_dump()
        writer.writerow([_csv_cell(data[column]) for column in CSV_COLUMNS])

    filename = f"utilization_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/faults", response_model=list[FaultSummaryRow])
async def fault_summary(session: SessionDep, user: CurrentUser) -> list[FaultSummaryRow]:
    stmt = (
        select(Machine.id, Machine.serial_number, FaultCode.severity, func.count(FaultCode.id))
        .join(FaultCode, and_(FaultCode.machine_id == Machine.id, FaultCode.is_active.is_(True)))
        .where(Machine.organization_id == user.organization_id)
        .group_by(Machine.id, Machine.serial_number, FaultCode.severity)
    )
    summary: dict[uuid.UUID, FaultSummaryRow] = {}
    for machine_id, serial_number, severity, count in await session.execute(stmt):
        row = summary.setdefault(
            machine_id, FaultSummaryRow(machine_id=machine_id, serial_number=serial_number)
        )
        match severity:
            case FaultSeverity.CRITICAL:
                row.critical = count
            case FaultSeverity.WARNING:
                row.warning = count
            case _:
                row.info = count
    return sorted(summary.values(), key=lambda r: (-r.critical, -r.warning, r.serial_number))
