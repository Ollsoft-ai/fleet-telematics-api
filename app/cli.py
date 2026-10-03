"""Operational commands: ``python -m app.cli <command>``."""

import argparse
import asyncio
import getpass
import logging
from collections.abc import Coroutine
from typing import Any

from app.auth import hash_secret
from app.config import get_settings
from app.db import Base, get_engine, get_sessionmaker
from app.models import Organization, OrganizationKind, User, UserRole
from app.services.retention import purge_expired

logger = logging.getLogger("app.cli")

MIN_PASSWORD_LENGTH = 12


async def init_db() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema is up to date")


async def create_admin(email: str, full_name: str, organization: str, kind: str) -> None:
    password = getpass.getpass("Password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match")

    async with get_sessionmaker()() as session:
        org = Organization(name=organization, kind=OrganizationKind(kind))
        session.add(org)
        await session.flush()
        user = User(
            organization_id=org.id,
            email=email.strip().lower(),
            full_name=full_name,
            password_hash=hash_secret(password),
            role=UserRole.ADMIN,
        )
        session.add(user)
        await session.commit()
    logger.info("Created organization %s with admin user %s", org.id, user.id)


async def purge_telemetry() -> None:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        await purge_expired(session, retention_days=settings.telemetry_retention_days)


async def _run(command: Coroutine[Any, Any, None]) -> None:
    try:
        await command
    finally:
        await get_engine().dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="create database tables")
    admin = commands.add_parser("create-admin", help="create an organization and its first admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--organization", required=True)
    admin.add_argument("--kind", choices=[k.value for k in OrganizationKind], default="contractor")
    commands.add_parser("purge-telemetry", help="delete data past the retention period")
    args = parser.parse_args(argv)

    logging.basicConfig(level=get_settings().log_level, format="%(levelname)s %(message)s")
    match args.command:
        case "init-db":
            asyncio.run(_run(init_db()))
        case "create-admin":
            asyncio.run(_run(create_admin(args.email, args.name, args.organization, args.kind)))
        case "purge-telemetry":
            asyncio.run(_run(purge_telemetry()))


if __name__ == "__main__":
    main()
