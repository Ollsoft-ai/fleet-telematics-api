"""Portal login."""

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.auth import (
    CurrentUser,
    create_access_token,
    hash_secret,
    needs_rehash,
    verify_dummy,
    verify_secret,
)
from app.config import SettingsDep
from app.db import SessionDep, utcnow
from app.models import User
from app.schemas.auth import LoginRequest, TokenResponse, UserRead

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/token", response_model=TokenResponse)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    now = utcnow()
    user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or not user.is_active:
        verify_dummy(body.password)
        raise _invalid_credentials()
    if user.locked_until is not None and user.locked_until > now:
        verify_dummy(body.password)
        logger.warning("Login attempt for locked account %s", user.id)
        raise _invalid_credentials()

    if not verify_secret(user.password_hash, body.password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.login_max_failed_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_attempts = 0
            logger.warning("Account %s locked after repeated failed logins", user.id)
        await session.commit()
        raise _invalid_credentials()

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_secret(body.password)
    await session.commit()

    token, expires_in = create_access_token(user, settings)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=UserRead)
async def read_current_user(user: CurrentUser) -> User:
    return user
