from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from tests.conftest import (
    ADMIN_PASSWORD,
    RegisteredMachine,
    make_machine,
    make_organization,
)


async def test_login_issues_token_for_valid_credentials(
    client: httpx.AsyncClient, admin: User
) -> None:
    response = await client.post(
        "/auth/token",
        json={"email": "  Dispatch@Stavby-Morava.example ", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == admin.email


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("dispatch@stavby-morava.example", "wrong-password"),
        ("nobody@stavby-morava.example", ADMIN_PASSWORD),
    ],
)
async def test_login_rejects_bad_credentials_with_same_response(
    client: httpx.AsyncClient, admin: User, email: str, password: str
) -> None:
    response = await client.post("/auth/token", json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


async def test_account_locks_after_repeated_failures(
    client: httpx.AsyncClient, session: AsyncSession, admin: User
) -> None:
    for _ in range(get_settings().login_max_failed_attempts):
        response = await client.post(
            "/auth/token", json={"email": admin.email, "password": "wrong-password"}
        )
        assert response.status_code == 401

    response = await client.post(
        "/auth/token", json={"email": admin.email, "password": ADMIN_PASSWORD}
    )

    assert response.status_code == 401
    await session.refresh(admin)
    assert admin.locked_until is not None


@pytest.mark.parametrize("problem", ["expired", "wrong_key", "wrong_issuer"])
async def test_invalid_tokens_are_rejected(
    client: httpx.AsyncClient, admin: User, problem: str
) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(admin.id),
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    key = settings.jwt_secret.get_secret_value()
    if problem == "expired":
        claims["iat"], claims["exp"] = now - timedelta(hours=2), now - timedelta(hours=1)
    elif problem == "wrong_key":
        key = "another-secret-that-is-long-enough-to-sign"
    else:
        claims["iss"] = "someone-else"
    token = jwt.encode(claims, key, algorithm=settings.jwt_algorithm)

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_machine_key_authenticates_terminal(
    client: httpx.AsyncClient, excavator: RegisteredMachine
) -> None:
    response = await client.post(
        "/telemetry",
        json={"samples": [{"recorded_at": datetime.now(UTC).isoformat(), "fuel_level_pct": 70}]},
        headers=excavator.headers,
    )

    assert response.status_code == 202


@pytest.mark.parametrize(
    "tamper",
    [
        lambda key: key[:-4] + ("AAAA" if not key.endswith("AAAA") else "BBBB"),
        lambda key: "ftk_000000000000_" + key.split("_", 2)[2],
        lambda key: key.replace("ftk_", "xyz_", 1),
        lambda key: "",
    ],
)
async def test_tampered_machine_keys_are_rejected(
    client: httpx.AsyncClient, excavator: RegisteredMachine, tamper: Callable[[str], str]
) -> None:
    response = await client.post(
        "/telemetry",
        json={"samples": [{"recorded_at": datetime.now(UTC).isoformat()}]},
        headers={"X-Machine-Key": tamper(excavator.api_key)},
    )

    assert response.status_code == 401


async def test_users_cannot_read_other_organizations_machines(
    client: httpx.AsyncClient, session: AsyncSession, auth_headers: dict[str, str]
) -> None:
    other_org = await make_organization(session, "Půjčovna Strojů Brno a.s.")
    foreign = await make_machine(session, other_org, "KX220-0009999")

    response = await client.get(f"/machines/{foreign.machine.id}", headers=auth_headers)

    assert response.status_code == 404


async def test_rotated_machine_key_replaces_the_old_one(
    client: httpx.AsyncClient, auth_headers: dict[str, str], excavator: RegisteredMachine
) -> None:
    rotated = await client.post(f"/machines/{excavator.machine.id}/api-key", headers=auth_headers)
    assert rotated.status_code == 200
    sample = {"samples": [{"recorded_at": datetime.now(UTC).isoformat()}]}

    old = await client.post("/telemetry", json=sample, headers=excavator.headers)
    new = await client.post(
        "/telemetry", json=sample, headers={"X-Machine-Key": rotated.json()["api_key"]}
    )

    assert old.status_code == 401
    assert new.status_code == 202


async def test_operator_signs_in_on_terminal_and_previous_session_ends(
    client: httpx.AsyncClient, auth_headers: dict[str, str], excavator: RegisteredMachine
) -> None:
    for badge in ("OP-1001", "OP-1002"):
        created = await client.post(
            "/operators",
            json={"badge_number": badge, "full_name": f"Operator {badge}", "pin": "482913"},
            headers=auth_headers,
        )
        assert created.status_code == 201

    first = await client.post(
        "/operator-sessions",
        json={"badge_number": "op-1001", "pin": "482913"},
        headers=excavator.headers,
    )
    second = await client.post(
        "/operator-sessions",
        json={"badge_number": "OP-1002", "pin": "482913"},
        headers=excavator.headers,
    )
    ended = await client.post(
        f"/operator-sessions/{first.json()['id']}/end", headers=excavator.headers
    )

    assert first.status_code == second.status_code == 201
    assert second.json()["operator_name"] == "Operator OP-1002"
    assert ended.json()["ended_at"] is not None


async def test_operator_is_locked_after_repeated_wrong_pins(
    client: httpx.AsyncClient, auth_headers: dict[str, str], excavator: RegisteredMachine
) -> None:
    await client.post(
        "/operators",
        json={"badge_number": "OP-2001", "full_name": "Operator OP-2001", "pin": "175320"},
        headers=auth_headers,
    )
    for _ in range(get_settings().login_max_failed_attempts):
        await client.post(
            "/operator-sessions",
            json={"badge_number": "OP-2001", "pin": "000000"},
            headers=excavator.headers,
        )

    response = await client.post(
        "/operator-sessions",
        json={"badge_number": "OP-2001", "pin": "175320"},
        headers=excavator.headers,
    )

    assert response.status_code == 401
