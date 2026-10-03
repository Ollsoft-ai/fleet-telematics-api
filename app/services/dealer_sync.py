"""Deliver service events to the dealer's service-management system.

Each request is signed: ``X-Fleet-Signature: sha256=HMAC(secret, "<timestamp>.<body>")``.
The dealer side de-duplicates on ``Idempotency-Key``.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Self

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import __version__
from app.config import Settings
from app.db import utcnow
from app.models import Machine, ServiceEvent

logger = logging.getLogger(__name__)

MAX_SYNC_ATTEMPTS = 8
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class DealerSyncError(Exception):
    """The dealer system did not accept a service event."""


class DealerClient:
    def __init__(
        self,
        url: str,
        secret: SecretStr,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._secret = secret
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._http = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": f"fleet-telematics-api/{__version__}"},
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        if settings.dealer_webhook_url is None or settings.dealer_webhook_secret is None:
            return None
        return cls(
            str(settings.dealer_webhook_url),
            settings.dealer_webhook_secret,
            timeout=settings.dealer_timeout_seconds,
            max_retries=settings.dealer_max_retries,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def sign(self, timestamp: str, body: bytes) -> str:
        key = self._secret.get_secret_value().encode()
        digest = hmac.new(key, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    async def send_service_event(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        last_error = "not attempted"
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "Idempotency-Key": str(payload["event_id"]),
                "X-Fleet-Timestamp": timestamp,
                "X-Fleet-Signature": self.sign(timestamp, body),
            }
            try:
                response = await self._http.post(self._url, content=body, headers=headers)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if response.is_success:
                return
            last_error = f"HTTP {response.status_code}"
            if response.status_code not in _RETRYABLE_STATUS:
                break
        raise DealerSyncError(last_error)


def build_payload(event: ServiceEvent, machine: Machine) -> dict[str, Any]:
    return {
        "event_id": str(event.id),
        "kind": event.kind.value,
        "title": event.title,
        "created_at": event.created_at.isoformat(),
        "engine_hours": round(event.engine_hours, 1),
        "machine": {
            "serial_number": machine.serial_number,
            "manufacturer": machine.manufacturer,
            "model": machine.model,
            "machine_type": machine.machine_type.value,
        },
        "details": event.details,
    }


async def flush_pending(
    session_factory: async_sessionmaker[AsyncSession],
    client: DealerClient,
    *,
    batch_size: int = 20,
) -> int:
    """Send unsynced service events, oldest first. Returns how many were accepted."""
    sent = 0
    async with session_factory() as session:
        stmt = (
            select(ServiceEvent, Machine)
            .join(Machine, Machine.id == ServiceEvent.machine_id)
            .where(
                ServiceEvent.dealer_synced_at.is_(None),
                ServiceEvent.dealer_sync_attempts < MAX_SYNC_ATTEMPTS,
            )
            .order_by(ServiceEvent.created_at)
            .limit(batch_size)
            .with_for_update(of=ServiceEvent, skip_locked=True)
        )
        for event, machine in await session.execute(stmt):
            event.dealer_sync_attempts += 1
            try:
                await client.send_service_event(build_payload(event, machine))
            except DealerSyncError as exc:
                event.dealer_last_error = str(exc)[:500]
                logger.warning("Dealer sync failed for service event %s: %s", event.id, exc)
            else:
                event.dealer_synced_at = utcnow()
                event.dealer_last_error = None
                sent += 1
        await session.commit()
    return sent
