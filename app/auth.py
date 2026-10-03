"""Authentication: JWT bearer tokens for portal users, API keys for machines."""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.config import Settings, SettingsDep
from app.db import SessionDep, utcnow
from app.models import Machine, User, UserRole

API_KEY_SCHEME = "ftk"

_hasher = PasswordHasher()
_dummy_hash = _hasher.hash(secrets.token_urlsafe(24))
_bearer = HTTPBearer(auto_error=False)
_machine_key = APIKeyHeader(name="X-Machine-Key", auto_error=False)


def hash_secret(value: str) -> str:
    """Hash a password or PIN with Argon2id."""
    return _hasher.hash(value)


def verify_secret(hashed: str, value: str) -> bool:
    try:
        return _hasher.verify(hashed, value)
    except (VerificationError, InvalidHashError):
        return False


def verify_dummy(value: str) -> None:
    """Spend the time of a real verification when there is no account to check against."""
    verify_secret(_dummy_hash, value)


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


@dataclass(frozen=True, slots=True)
class MachineApiKey:
    token: str
    prefix: str
    digest: str


def issue_machine_api_key() -> MachineApiKey:
    """Generate a machine key. Store ``prefix`` and ``digest``; ``token`` is shown once."""
    prefix = secrets.token_hex(6)
    token = f"{API_KEY_SCHEME}_{prefix}_{secrets.token_urlsafe(32)}"
    return MachineApiKey(token=token, prefix=prefix, digest=_digest(token))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user: User, settings: Settings) -> tuple[str, int]:
    now = utcnow()
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "org": str(user.organization_id),
        "iss": settings.jwt_issuer,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, int(ttl.total_seconds())


def _unauthorized(detail: str, scheme: str = "Bearer") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": scheme})


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        raise _unauthorized("Invalid or expired token") from None

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    allowed = frozenset(roles)

    async def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return dependency


AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
ManagerUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.FLEET_MANAGER))]


async def get_current_machine(
    session: SessionDep,
    api_key: Annotated[str | None, Security(_machine_key)],
) -> Machine:
    if not api_key:
        raise _unauthorized("Machine API key required", scheme="ApiKey")
    scheme, _, rest = api_key.partition("_")
    prefix, _, _ = rest.partition("_")
    if scheme != API_KEY_SCHEME or not prefix:
        raise _unauthorized("Invalid machine API key", scheme="ApiKey")

    machine = await session.scalar(select(Machine).where(Machine.api_key_prefix == prefix))
    if machine is None or not hmac.compare_digest(machine.api_key_hash, _digest(api_key)):
        raise _unauthorized("Invalid machine API key", scheme="ApiKey")
    if not machine.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Machine is deactivated")
    return machine


CurrentMachine = Annotated[Machine, Depends(get_current_machine)]
