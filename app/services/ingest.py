"""Telemetry ingest: store samples, track fault codes and update machine state."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import FaultCode, FaultSeverity, Machine, TelemetryRecord
from app.schemas.telemetry import FaultCodeIn, LampStatus, TelemetryBatch, TelemetrySample
from app.services.geofence_alerts import GeofenceEvaluator

logger = logging.getLogger(__name__)

MAX_CLOCK_SKEW = timedelta(minutes=5)

_LAMP_SEVERITY = {
    LampStatus.RED_STOP: FaultSeverity.CRITICAL,
    LampStatus.AMBER_WARNING: FaultSeverity.WARNING,
    LampStatus.PROTECT: FaultSeverity.WARNING,
    LampStatus.MALFUNCTION: FaultSeverity.WARNING,
    LampStatus.NONE: FaultSeverity.INFO,
}


@dataclass(slots=True)
class IngestOutcome:
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0


async def ingest_batch(
    session: AsyncSession,
    machine: Machine,
    batch: TelemetryBatch,
    *,
    now: datetime | None = None,
) -> IngestOutcome:
    """Add a batch of samples from one machine to the session without committing.

    Samples are idempotent on ``(machine, recorded_at)``, so terminals can resend
    a buffer after a connectivity gap.
    """
    now = now or utcnow()
    outcome = IngestOutcome()
    samples: list[TelemetrySample] = []
    for sample in sorted(batch.samples, key=lambda s: s.recorded_at):
        if sample.recorded_at > now + MAX_CLOCK_SKEW:
            outcome.rejected += 1
        else:
            samples.append(sample)
    if not samples:
        return outcome

    stored = await _stored_timestamps(session, machine.id, samples)
    faults = await _load_faults(session, machine.id)
    geofences = await GeofenceEvaluator.load(session, machine)

    latest: TelemetrySample | None = None
    for sample in samples:
        recorded_at = sample.recorded_at.astimezone(UTC)
        if recorded_at in stored:
            outcome.duplicates += 1
            continue
        stored.add(recorded_at)
        session.add(_to_record(machine.id, sample, recorded_at, now))
        for code in sample.fault_codes or ():
            _upsert_fault(session, machine.id, faults, code, recorded_at)
        if sample.latitude is not None and sample.longitude is not None:
            geofences.evaluate(sample.latitude, sample.longitude, recorded_at)
        _update_machine_state(machine, sample, recorded_at)
        latest = sample
        outcome.accepted += 1

    if latest is not None and latest.fault_codes is not None:
        _clear_resolved_faults(faults, latest, latest.recorded_at.astimezone(UTC))

    logger.info(
        "Telemetry from machine %s: %d accepted, %d duplicate, %d rejected",
        machine.id,
        outcome.accepted,
        outcome.duplicates,
        outcome.rejected,
    )
    return outcome


async def _stored_timestamps(
    session: AsyncSession, machine_id: uuid.UUID, samples: list[TelemetrySample]
) -> set[datetime]:
    timestamps = [s.recorded_at.astimezone(UTC) for s in samples]
    stmt = select(TelemetryRecord.recorded_at).where(
        TelemetryRecord.machine_id == machine_id,
        TelemetryRecord.recorded_at.in_(timestamps),
    )
    return set((await session.scalars(stmt)).all())


async def _load_faults(
    session: AsyncSession, machine_id: uuid.UUID
) -> dict[tuple[int, int], FaultCode]:
    rows = await session.scalars(select(FaultCode).where(FaultCode.machine_id == machine_id))
    return {(fault.spn, fault.fmi): fault for fault in rows}


def _to_record(
    machine_id: uuid.UUID, sample: TelemetrySample, recorded_at: datetime, received_at: datetime
) -> TelemetryRecord:
    return TelemetryRecord(
        machine_id=machine_id,
        recorded_at=recorded_at,
        received_at=received_at,
        latitude=sample.latitude,
        longitude=sample.longitude,
        speed_kmh=sample.speed_kmh,
        engine_hours=sample.engine_hours,
        fuel_level_pct=sample.fuel_level_pct,
        hydraulic_pressure_bar=sample.hydraulic_pressure_bar,
        coolant_temp_c=sample.coolant_temp_c,
    )


def _upsert_fault(
    session: AsyncSession,
    machine_id: uuid.UUID,
    faults: dict[tuple[int, int], FaultCode],
    code: FaultCodeIn,
    seen_at: datetime,
) -> tuple[FaultCode, bool]:
    """Record a reported DTC. Returns the fault and whether it just became active."""
    severity = _LAMP_SEVERITY[code.lamp]
    fault = faults.get((code.spn, code.fmi))
    if fault is None:
        fault = FaultCode(
            id=uuid.uuid4(),
            machine_id=machine_id,
            spn=code.spn,
            fmi=code.fmi,
            source_address=code.source_address,
            severity=severity,
            occurrence_count=code.occurrence_count,
            is_active=True,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )
        session.add(fault)
        faults[(code.spn, code.fmi)] = fault
        return fault, True

    activated = not fault.is_active
    fault.is_active = True
    fault.cleared_at = None
    fault.severity = severity
    fault.occurrence_count = max(fault.occurrence_count, code.occurrence_count)
    fault.last_seen_at = max(fault.last_seen_at, seen_at)
    return fault, activated


def _clear_resolved_faults(
    faults: dict[tuple[int, int], FaultCode], latest: TelemetrySample, at: datetime
) -> None:
    reported = {(code.spn, code.fmi) for code in latest.fault_codes or ()}
    for key, fault in faults.items():
        if fault.is_active and key not in reported:
            fault.is_active = False
            fault.cleared_at = at


def _update_machine_state(machine: Machine, sample: TelemetrySample, recorded_at: datetime) -> None:
    if sample.engine_hours is not None and sample.engine_hours > machine.engine_hours:
        machine.engine_hours = sample.engine_hours
    if machine.last_seen_at is not None and recorded_at < machine.last_seen_at:
        return
    machine.last_seen_at = recorded_at
    if sample.latitude is not None and sample.longitude is not None:
        machine.last_latitude = sample.latitude
        machine.last_longitude = sample.longitude
    if sample.fuel_level_pct is not None:
        machine.fuel_level_pct = sample.fuel_level_pct
