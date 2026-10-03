from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.config import get_settings
from app.models import User, UserRole
from tests.conftest import RegisteredMachine, make_machine, make_organization


async def test_manager_shares_live_location_without_customer_login(
    client: httpx.AsyncClient,
    session: AsyncSession,
    admin: User,
    excavator: RegisteredMachine,
) -> None:
    admin.role = UserRole.FLEET_MANAGER
    await session.commit()
    token, _ = create_access_token(admin, get_settings())
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(f"/machines/{excavator.machine.id}/location-share", headers=headers)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    url = created.json()["url"]
    assert url.startswith("http://testserver/shared/machines/location#token=")
    expires_at = datetime.fromisoformat(created.json()["expires_at"])
    assert timedelta(hours=23, minutes=59) < expires_at - datetime.now(UTC) <= timedelta(hours=24)

    path = urlsplit(url).path
    share_key = parse_qs(urlsplit(url).fragment)["token"][0]
    page = await client.get(path)
    assert page.status_code == 200
    assert page.headers["referrer-policy"] == "no-referrer"
    assert share_key not in page.text
    assert 'headers: {"X-Location-Share-Key": key}' in page.text
    data_path = f"{path}/data"
    share_headers = {"X-Location-Share-Key": share_key}
    assert (await client.get(data_path)).status_code == 404
    before = await client.get(data_path, headers=share_headers)
    assert before.status_code == 200
    assert before.json() == {"latitude": None, "longitude": None, "last_seen_at": None}
    assert before.headers["cache-control"] == "no-store"

    timestamp = datetime.now(UTC)
    ingested = await client.post(
        "/telemetry",
        json={
            "samples": [
                {"recorded_at": timestamp.isoformat(), "latitude": 49.1951, "longitude": 16.6068}
            ]
        },
        headers=excavator.headers,
    )
    assert ingested.status_code == 202
    after = await client.get(data_path, headers=share_headers)
    assert after.status_code == 200
    assert after.json()["latitude"] == 49.1951
    assert after.json()["longitude"] == 16.6068
    assert datetime.fromisoformat(after.json()["last_seen_at"]) == timestamp
    assert set(after.json()) == {"latitude", "longitude", "last_seen_at"}


async def test_share_creation_requires_manager_and_org_ownership(
    client: httpx.AsyncClient,
    session: AsyncSession,
    admin: User,
    excavator: RegisteredMachine,
    auth_headers: dict[str, str],
) -> None:
    path = f"/machines/{excavator.machine.id}/location-share"
    assert (await client.post(path)).status_code == 401
    admin.role = UserRole.VIEWER
    await session.commit()
    assert (await client.post(path, headers=auth_headers)).status_code == 403
    admin.role = UserRole.FLEET_MANAGER
    await session.commit()
    other_org = await make_organization(session, "Other Fleet")
    foreign = await make_machine(session, other_org, "KX220-OTHER")
    assert (
        await client.post(f"/machines/{foreign.machine.id}/location-share", headers=auth_headers)
    ).status_code == 404


async def test_share_link_cannot_be_reused_for_other_purposes_or_after_expiry(
    client: httpx.AsyncClient,
    session: AsyncSession,
    auth_headers: dict[str, str],
    excavator: RegisteredMachine,
) -> None:
    created = await client.post(
        f"/machines/{excavator.machine.id}/location-share", headers=auth_headers
    )
    link = urlsplit(created.json()["url"])
    path = f"{link.path}/data"
    token = parse_qs(link.fragment)["token"][0]
    assert (await client.get(path, headers={"X-Location-Share-Key": token})).status_code == 200
    assert (
        await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 401

    settings = get_settings()
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        audience="machine-location-share",
        issuer=settings.jwt_issuer,
    )
    claims["iat"] = datetime.now(UTC) - timedelta(days=2)
    claims["nbf"] = claims["iat"]
    claims["exp"] = datetime.now(UTC) - timedelta(days=1)
    expired = jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    assert (await client.get(path, headers={"X-Location-Share-Key": expired})).status_code == 404
    assert (
        await client.get(path, headers={"X-Location-Share-Key": token[:-4] + "xxxx"})
    ).status_code == 404
    assert (await client.get(path, headers={"X-Location-Share-Key": "invalid"})).status_code == 404

    excavator.machine.is_active = False
    await session.commit()
    assert (await client.get(path, headers={"X-Location-Share-Key": token})).status_code == 404
    assert (
        await client.post(f"/machines/{excavator.machine.id}/location-share", headers=auth_headers)
    ).status_code == 404
