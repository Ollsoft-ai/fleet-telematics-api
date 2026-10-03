import os
import secrets

os.environ.setdefault("FLEET_DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("FLEET_JWT_SECRET", secrets.token_urlsafe(48))
os.environ.setdefault("FLEET_ENVIRONMENT", "development")

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import hash_secret, issue_machine_api_key
from app.db import Base, get_session
from app.main import create_app
from app.models import (
    Machine,
    MachineType,
    Organization,
    OrganizationKind,
    User,
    UserRole,
)

ADMIN_PASSWORD = "gravel-pit-north-2026"


@dataclass
class RegisteredMachine:
    machine: Machine
    api_key: str

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Machine-Key": self.api_key}


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fleet.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def make_organization(session: AsyncSession, name: str) -> Organization:
    organization = Organization(name=name, kind=OrganizationKind.CONTRACTOR)
    session.add(organization)
    await session.commit()
    return organization


async def make_machine(
    session: AsyncSession, organization: Organization, serial_number: str
) -> RegisteredMachine:
    key = issue_machine_api_key()
    machine = Machine(
        organization_id=organization.id,
        serial_number=serial_number,
        manufacturer="Kovo Machinery",
        model="KX-220",
        machine_type=MachineType.EXCAVATOR,
        api_key_prefix=key.prefix,
        api_key_hash=key.digest,
        engine_hours=1240.0,
    )
    session.add(machine)
    await session.commit()
    return RegisteredMachine(machine=machine, api_key=key.token)


@pytest.fixture
async def organization(session: AsyncSession) -> Organization:
    return await make_organization(session, "Stavby Morava s.r.o.")


@pytest.fixture
async def admin(session: AsyncSession, organization: Organization) -> User:
    user = User(
        organization_id=organization.id,
        email="dispatch@stavby-morava.example",
        full_name="Jana Dvořáková",
        password_hash=hash_secret(ADMIN_PASSWORD),
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def auth_headers(client: httpx.AsyncClient, admin: User) -> dict[str, str]:
    response = await client.post(
        "/auth/token", json={"email": admin.email, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def excavator(session: AsyncSession, organization: Organization) -> RegisteredMachine:
    return await make_machine(session, organization, "KX220-0001234")
