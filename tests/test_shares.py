from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.config import get_settings
from app.db import utcnow
from app.models import MachineShare, User, UserRole
from tests.conftest import RegisteredMachine, make_machine, make_organization


def share_token(url: str) -> str:
    return parse_qs(urlsplit(url).fragment)["token"][0]


async def test_manager_share_shows_updated_location_without_login_and_expires(
    client: httpx.AsyncClient,
    session: AsyncSession,
    excavator: RegisteredMachine,
    admin: User,
) -> None:
    admin.role = UserRole.FLEET_MANAGER
    await session.commit()
    token, _ = create_access_token(admin, get_settings())
    created = await client.post(
        f"/machines/{excavator.machine.id}/shares",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201
    url = created.json()["url"]
    assert url.startswith("http://testserver/shares#token=")
    assert datetime.fromisoformat(created.json()["expires_at"]) - utcnow() <= timedelta(hours=24)
    assert datetime.fromisoformat(created.json()["expires_at"]) - utcnow() > timedelta(hours=23)
    page = await client.get(url)
    assert page.status_code == 200
    assert "X-Share-Token" in page.text
    assert page.headers["cache-control"] == "no-cache, no-store"

    headers = {"X-Share-Token": share_token(url)}
    initial = await client.get("/shares/location", headers=headers)
    assert initial.status_code == 200
    assert initial.json() == {
        "machine_id": str(excavator.machine.id),
        "latitude": None,
        "longitude": None,
        "last_seen_at": None,
    }

    observed_at = datetime.now(UTC) - timedelta(seconds=1)
    ingested = await client.post(
        "/telemetry",
        json={
            "samples": [
                {"recorded_at": observed_at.isoformat(), "latitude": 49.2, "longitude": 16.6}
            ]
        },
        headers=excavator.headers,
    )
    assert ingested.status_code == 202
    updated = await client.get("/shares/location", headers=headers)
    assert updated.status_code == 200
    assert updated.json()["latitude"] == 49.2
    assert updated.json()["longitude"] == 16.6
    assert datetime.fromisoformat(updated.json()["last_seen_at"]) == observed_at
    assert updated.headers["cache-control"] == "no-cache, no-store"

    share = await session.scalar(select(MachineShare))
    assert share is not None
    assert share.token_hash != share_token(url)
    share.expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()
    assert (await client.get("/shares/location", headers=headers)).status_code == 404


async def test_shares_restrict_creation_and_reject_invalid_links(
    client: httpx.AsyncClient,
    session: AsyncSession,
    excavator: RegisteredMachine,
    auth_headers: dict[str, str],
    admin: User,
) -> None:
    endpoint = f"/machines/{excavator.machine.id}/shares"
    assert (await client.post(endpoint)).status_code == 401
    other_org = await make_organization(session, "Other fleet")
    other_machine = await make_machine(session, other_org, "OTHER-123")
    assert (
        await client.post(f"/machines/{other_machine.machine.id}/shares", headers=auth_headers)
    ).status_code == 404

    admin.role = UserRole.VIEWER
    await session.commit()
    assert (await client.post(endpoint, headers=auth_headers)).status_code == 403
    assert (await client.get("/shares/location")).status_code == 404
    assert (
        await client.get("/shares/location", headers={"X-Share-Token": "invalid"})
    ).status_code == 404
