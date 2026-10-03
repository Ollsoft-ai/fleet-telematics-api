"""Delete telemetry and operator-related data past the retention period."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import FaultCode, GeofenceEvent, OperatorSession, TelemetryRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurgeResult:
    telemetry_records: int
    geofence_events: int
    operator_sessions: int
    cleared_faults: int


async def purge_expired(
    session: AsyncSession,
    *,
    retention_days: int,
    now: datetime | None = None,
    batch_size: int = 5000,
) -> PurgeResult:
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    result = PurgeResult(
        telemetry_records=await _delete_in_batches(
            session, TelemetryRecord, TelemetryRecord.recorded_at < cutoff, batch_size
        ),
        geofence_events=await _delete_in_batches(
            session, GeofenceEvent, GeofenceEvent.occurred_at < cutoff, batch_size
        ),
        operator_sessions=await _delete_in_batches(
            session,
            OperatorSession,
            OperatorSession.ended_at.is_not(None) & (OperatorSession.ended_at < cutoff),
            batch_size,
        ),
        cleared_faults=await _delete_in_batches(
            session,
            FaultCode,
            FaultCode.is_active.is_(False) & (FaultCode.cleared_at < cutoff),
            batch_size,
        ),
    )
    logger.info("Retention purge before %s: %s", cutoff.isoformat(), result)
    return result


async def _delete_in_batches(
    session: AsyncSession,
    model: type[TelemetryRecord | GeofenceEvent | OperatorSession | FaultCode],
    condition: ColumnElement[bool],
    batch_size: int,
) -> int:
    total = 0
    while True:
        ids = select(model.id).where(condition).limit(batch_size).scalar_subquery()
        stmt = delete(model).where(model.id.in_(ids)).execution_options(synchronize_session=False)
        result = cast(CursorResult[Any], await session.execute(stmt))
        await session.commit()
        total += result.rowcount
        if result.rowcount < batch_size:
            return total
