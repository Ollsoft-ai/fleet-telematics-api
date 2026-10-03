from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FaultSeverity, TelemetryRecord
from app.services.retention import purge_expired
from tests.conftest import RegisteredMachine


def sample(at: datetime, **fields: Any) -> dict[str, Any]:
    return {"recorded_at": at.isoformat(), **fields}


async def post_samples(
    client: httpx.AsyncClient, machine: RegisteredMachine, *samples: dict[str, Any]
) -> httpx.Response:
    return await client.post("/telemetry", json={"samples": list(samples)}, headers=machine.headers)


async def test_ingest_updates_last_known_machine_state(
    client: httpx.AsyncClient, session: AsyncSession, excavator: RegisteredMachine
) -> None:
    now = datetime.now(UTC)

    response = await post_samples(
        client,
        excavator,
        sample(
            now - timedelta(minutes=5),
            latitude=49.1951,
            longitude=16.6068,
            engine_hours=1241.6,
            fuel_level_pct=61.5,
        ),
        sample(
            now - timedelta(minutes=10),
            latitude=49.1950,
            longitude=16.6060,
            engine_hours=1241.5,
            fuel_level_pct=62.0,
        ),
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": 2, "duplicates": 0, "rejected": 0}
    machine = excavator.machine
    await session.refresh(machine)
    assert machine.engine_hours == 1241.6
    assert (machine.last_latitude, machine.last_longitude) == (49.1951, 16.6068)
    assert machine.fuel_level_pct == 61.5


async def test_resent_samples_are_not_stored_twice(
    client: httpx.AsyncClient, session: AsyncSession, excavator: RegisteredMachine
) -> None:
    at = datetime.now(UTC) - timedelta(minutes=1)
    await post_samples(client, excavator, sample(at, engine_hours=1241.0))

    response = await post_samples(client, excavator, sample(at, engine_hours=1241.0))

    assert response.json() == {"accepted": 0, "duplicates": 1, "rejected": 0}
    count = await session.scalar(select(func.count()).select_from(TelemetryRecord))
    assert count == 1


async def test_samples_from_the_future_are_rejected(
    client: httpx.AsyncClient, excavator: RegisteredMachine
) -> None:
    response = await post_samples(
        client, excavator, sample(datetime.now(UTC) + timedelta(hours=1), fuel_level_pct=50)
    )

    assert response.json() == {"accepted": 0, "duplicates": 0, "rejected": 1}


async def test_backfilled_samples_do_not_overwrite_newer_state(
    client: httpx.AsyncClient, session: AsyncSession, excavator: RegisteredMachine
) -> None:
    now = datetime.now(UTC)
    await post_samples(
        client, excavator, sample(now, latitude=49.20, longitude=16.61, engine_hours=1250.0)
    )

    await post_samples(
        client,
        excavator,
        sample(now - timedelta(hours=3), latitude=49.10, longitude=16.50, engine_hours=1247.0),
    )

    machine = excavator.machine
    await session.refresh(machine)
    assert machine.engine_hours == 1250.0
    assert machine.last_latitude == 49.20


async def test_fault_codes_follow_the_reported_active_set(
    client: httpx.AsyncClient, auth_headers: dict[str, str], excavator: RegisteredMachine
) -> None:
    now = datetime.now(UTC)
    hydraulic_temp = {"spn": 1638, "fmi": 0, "lamp": "red_stop"}
    air_filter = {"spn": 107, "fmi": 15, "lamp": "amber_warning"}
    await post_samples(
        client,
        excavator,
        sample(now - timedelta(minutes=2), fault_codes=[hydraulic_temp, air_filter]),
    )
    await post_samples(
        client, excavator, sample(now - timedelta(minutes=1), fault_codes=[air_filter])
    )

    response = await client.get(f"/machines/{excavator.machine.id}/faults", headers=auth_headers)
    history = await client.get(
        f"/machines/{excavator.machine.id}/faults?active_only=false", headers=auth_headers
    )

    active = response.json()
    assert [(f["spn"], f["severity"]) for f in active] == [(107, FaultSeverity.WARNING)]
    assert len(history.json()) == 2


@pytest.mark.parametrize(
    "fields",
    [
        {"latitude": 49.19},
        {"latitude": 91.0, "longitude": 16.6},
        {"fuel_level_pct": 140},
        {"fault_codes": [{"spn": 600_000, "fmi": 3}]},
    ],
)
async def test_invalid_samples_fail_validation(
    client: httpx.AsyncClient, excavator: RegisteredMachine, fields: dict[str, Any]
) -> None:
    response = await post_samples(client, excavator, sample(datetime.now(UTC), **fields))

    assert response.status_code == 422


async def test_naive_timestamps_are_rejected(
    client: httpx.AsyncClient, excavator: RegisteredMachine
) -> None:
    response = await post_samples(client, excavator, {"recorded_at": "2026-09-30T08:15:00"})

    assert response.status_code == 422


async def test_retention_purges_old_telemetry(
    client: httpx.AsyncClient, session: AsyncSession, excavator: RegisteredMachine
) -> None:
    now = datetime.now(UTC)
    await post_samples(
        client,
        excavator,
        sample(now - timedelta(days=200), engine_hours=900.0),
        sample(now - timedelta(days=1), engine_hours=1241.0),
    )

    result = await purge_expired(session, retention_days=180, now=now)

    assert result.telemetry_records == 1
    remaining = await session.scalars(select(TelemetryRecord.engine_hours))
    assert remaining.all() == [1241.0]
