import math
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_secret
from app.models import Organization, User, UserRole
from app.services.geofence_alerts import point_in_polygon
from tests.conftest import ADMIN_PASSWORD, RegisteredMachine

# Gravel pit north of Brno, [lon, lat].
SITE = [[16.600, 49.190], [16.615, 49.190], [16.615, 49.200], [16.600, 49.200]]
L_SHAPE = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]]


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (16.605, 49.195, True),
        (16.620, 49.195, False),
        (16.605, 49.185, False),
    ],
)
def test_point_in_rectangle(lon: float, lat: float, expected: bool) -> None:
    assert point_in_polygon(lon, lat, SITE) is expected


def test_point_in_concave_polygon() -> None:
    assert point_in_polygon(0.5, 3.0, L_SHAPE)
    assert point_in_polygon(3.0, 0.5, L_SHAPE)
    assert not point_in_polygon(3.0, 3.0, L_SHAPE)


def circle(vertices: int) -> list[list[float]]:
    return [
        [
            16.6 + 0.01 * math.cos(2 * math.pi * i / vertices),
            49.19 + 0.01 * math.sin(2 * math.pi * i / vertices),
        ]
        for i in range(vertices)
    ]


@pytest.mark.parametrize(
    "polygon",
    [
        [[16.60, 49.19], [16.61, 49.19]],
        [[16.60, 49.19], [16.61, 49.19], [16.61, 95.0]],
        [[16.60, 49.19], [16.61, 49.19], [16.62, 49.19]],
        [[16.60, 49.19], [16.61, 49.19], [16.60, 49.19], [16.61, 49.20], [16.60, 49.20]],
        [[179.9, 0.0], [-179.9, 0.0], [-179.9, 1.0]],
        circle(600),
    ],
    ids=[
        "two-vertices",
        "latitude-out-of-range",
        "collinear",
        "duplicate",
        "antimeridian",
        "too-many-vertices",
    ],
)
async def test_invalid_polygons_are_rejected(
    client: httpx.AsyncClient, auth_headers: dict[str, str], polygon: list[list[float]]
) -> None:
    response = await client.post(
        "/geofences", json={"name": "Site", "polygon": polygon}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_closing_vertex_is_optional(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/geofences",
        json={"name": "Gravel pit", "polygon": [*SITE, SITE[0]]},
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()["polygon"] == SITE


async def test_machine_entering_and_leaving_site_creates_events(
    client: httpx.AsyncClient, auth_headers: dict[str, str], excavator: RegisteredMachine
) -> None:
    created = await client.post(
        "/geofences", json={"name": "Gravel pit", "polygon": SITE}, headers=auth_headers
    )
    geofence_id = created.json()["id"]
    start = datetime.now(UTC) - timedelta(minutes=30)
    positions = [(49.185, 16.605), (49.195, 16.605), (49.196, 16.606), (49.195, 16.620)]

    await client.post(
        "/telemetry",
        json={
            "samples": [
                {
                    "recorded_at": (start + timedelta(minutes=i)).isoformat(),
                    "latitude": lat,
                    "longitude": lon,
                }
                for i, (lat, lon) in enumerate(positions)
            ]
        },
        headers=excavator.headers,
    )
    events = await client.get(f"/geofences/{geofence_id}/events", headers=auth_headers)

    assert [e["event_type"] for e in events.json()] == ["enter", "exit"]


async def test_viewers_cannot_create_geofences(
    client: httpx.AsyncClient, session: AsyncSession, organization: Organization
) -> None:
    session.add(
        User(
            organization_id=organization.id,
            email="site-office@stavby-morava.example",
            full_name="Site Office",
            password_hash=hash_secret(ADMIN_PASSWORD),
            role=UserRole.VIEWER,
        )
    )
    await session.commit()
    login = await client.post(
        "/auth/token",
        json={"email": "site-office@stavby-morava.example", "password": ADMIN_PASSWORD},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.post(
        "/geofences", json={"name": "Site", "polygon": SITE}, headers=headers
    )

    assert response.status_code == 403
