import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import ServiceEvent, ServiceEventKind
from app.services.dealer_sync import DealerClient, DealerSyncError, flush_pending
from app.services.maintenance import due_interval
from tests.conftest import RegisteredMachine

SECRET = "dealer-shared-secret-0123456789abcdef"


def dealer(handler: Callable[[httpx.Request], httpx.Response]) -> DealerClient:
    return DealerClient(
        "https://dealer.example.com/api/service-events",
        SecretStr(SECRET),
        max_retries=3,
        backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )


async def test_requests_are_signed_and_idempotent() -> None:
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(202)

    await dealer(handler).send_service_event({"event_id": "evt-1", "kind": "fault"})

    request = received[0]
    signed = request.headers["X-Fleet-Timestamp"].encode() + b"." + request.content
    expected = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
    assert request.headers["X-Fleet-Signature"] == f"sha256={expected}"
    assert request.headers["Idempotency-Key"] == "evt-1"


async def test_transient_failures_are_retried() -> None:
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses))

    await dealer(handler).send_service_event({"event_id": "evt-2"})


async def test_client_errors_are_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400)

    with pytest.raises(DealerSyncError, match="HTTP 400"):
        await dealer(handler).send_service_event({"event_id": "evt-3"})
    assert calls == 1


async def test_flush_marks_delivered_events(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    excavator: RegisteredMachine,
) -> None:
    session.add(
        ServiceEvent(
            machine_id=excavator.machine.id,
            kind=ServiceEventKind.SCHEDULED_SERVICE,
            title="250 h service: engine oil and oil filter",
            engine_hours=1250.2,
        )
    )
    await session.commit()

    sent = await flush_pending(session_factory, dealer(lambda request: httpx.Response(201)))

    assert sent == 1
    event = await session.scalar(select(ServiceEvent))
    assert event is not None
    await session.refresh(event)
    assert event.dealer_synced_at is not None
    assert event.dealer_sync_attempts == 1


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (240.0, 251.0, 250),
        (499.0, 501.0, 500),
        (990.0, 1010.0, 1000),
        (1999.5, 2000.0, 2000),
        (100.0, 200.0, None),
        (250.0, 250.0, None),
    ],
)
def test_due_interval(previous: float, current: float, expected: int | None) -> None:
    interval = due_interval(previous, current)

    assert (interval.hours if interval else None) == expected


async def test_crossing_an_interval_raises_a_service_event(
    client: httpx.AsyncClient, session: AsyncSession, excavator: RegisteredMachine
) -> None:
    at = datetime.now(UTC) - timedelta(minutes=1)
    await client.post(
        "/telemetry",
        json={"samples": [{"recorded_at": at.isoformat(), "engine_hours": 1250.2}]},
        headers=excavator.headers,
    )

    events = (await session.scalars(select(ServiceEvent))).all()
    assert [(e.kind, e.details["interval_hours"]) for e in events] == [
        (ServiceEventKind.SCHEDULED_SERVICE, 250)
    ]


def test_plain_http_dealer_webhook_is_refused() -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings(
            database_url="sqlite+aiosqlite://",
            jwt_secret=SecretStr("x" * 48),
            dealer_webhook_url="http://dealer.example.com/hooks",
            dealer_webhook_secret=SecretStr(SECRET),
        )
