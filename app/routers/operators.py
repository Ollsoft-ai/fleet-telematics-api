"""Machine operators and sign-in on the machine terminal."""

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    CurrentMachine,
    CurrentUser,
    ManagerUser,
    hash_secret,
    verify_dummy,
    verify_secret,
)
from app.config import SettingsDep
from app.db import SessionDep, utcnow
from app.models import Operator, OperatorSession, User
from app.schemas.operator import (
    OperatorCreate,
    OperatorRead,
    OperatorSessionRead,
    OperatorUpdate,
    TerminalSession,
    TerminalSignIn,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["operators"])


async def _get_org_operator(session: AsyncSession, user: User, operator_id: uuid.UUID) -> Operator:
    operator = await session.get(Operator, operator_id)
    if operator is None or operator.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Operator not found")
    return operator


@router.get("/operators", response_model=list[OperatorRead])
async def list_operators(
    session: SessionDep, user: CurrentUser, include_inactive: bool = False
) -> Sequence[Operator]:
    stmt = select(Operator).where(Operator.organization_id == user.organization_id)
    if not include_inactive:
        stmt = stmt.where(Operator.is_active.is_(True))
    return (await session.scalars(stmt.order_by(Operator.badge_number))).all()


@router.post("/operators", response_model=OperatorRead, status_code=status.HTTP_201_CREATED)
async def create_operator(body: OperatorCreate, session: SessionDep, user: ManagerUser) -> Operator:
    operator = Operator(
        organization_id=user.organization_id,
        badge_number=body.badge_number,
        full_name=body.full_name,
        pin_hash=hash_secret(body.pin),
    )
    session.add(operator)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Badge number already in use") from None
    await session.refresh(operator)
    return operator


@router.patch("/operators/{operator_id}", response_model=OperatorRead)
async def update_operator(
    operator_id: uuid.UUID, body: OperatorUpdate, session: SessionDep, user: ManagerUser
) -> Operator:
    operator = await _get_org_operator(session, user, operator_id)
    if body.full_name is not None:
        operator.full_name = body.full_name
    if body.is_active is not None:
        operator.is_active = body.is_active
    if body.pin is not None:
        operator.pin_hash = hash_secret(body.pin)
        operator.failed_pin_attempts = 0
        operator.locked_until = None
    await session.commit()
    return operator


def _sign_in_rejected() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown badge or wrong PIN")


async def _close_open_sessions(session: AsyncSession, machine_id: uuid.UUID, at: datetime) -> None:
    await session.execute(
        update(OperatorSession)
        .where(OperatorSession.machine_id == machine_id, OperatorSession.ended_at.is_(None))
        .values(ended_at=at)
    )


@router.post(
    "/operator-sessions", response_model=TerminalSession, status_code=status.HTTP_201_CREATED
)
async def sign_in_operator(
    body: TerminalSignIn, session: SessionDep, settings: SettingsDep, machine: CurrentMachine
) -> TerminalSession:
    """Operator signs in on the machine terminal with badge and PIN."""
    now = utcnow()
    operator = await session.scalar(
        select(Operator).where(
            Operator.organization_id == machine.organization_id,
            Operator.badge_number == body.badge_number.strip().upper(),
        )
    )
    if (
        operator is None
        or not operator.is_active
        or (operator.locked_until is not None and operator.locked_until > now)
    ):
        verify_dummy(body.pin)
        raise _sign_in_rejected()

    if not verify_secret(operator.pin_hash, body.pin):
        operator.failed_pin_attempts += 1
        if operator.failed_pin_attempts >= settings.login_max_failed_attempts:
            operator.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            operator.failed_pin_attempts = 0
            logger.warning("Operator %s locked after repeated wrong PINs", operator.id)
        await session.commit()
        raise _sign_in_rejected()

    operator.failed_pin_attempts = 0
    await _close_open_sessions(session, machine.id, now)
    op_session = OperatorSession(operator_id=operator.id, machine_id=machine.id, started_at=now)
    session.add(op_session)
    await session.commit()
    logger.info("Operator session %s started on machine %s", op_session.id, machine.id)
    return TerminalSession(
        **OperatorSessionRead.model_validate(op_session).model_dump(),
        operator_name=operator.full_name,
    )


@router.post("/operator-sessions/{session_id}/end", response_model=OperatorSessionRead)
async def end_operator_session(
    session_id: uuid.UUID, session: SessionDep, machine: CurrentMachine
) -> OperatorSession:
    op_session = await session.get(OperatorSession, session_id)
    if op_session is None or op_session.machine_id != machine.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if op_session.ended_at is None:
        op_session.ended_at = utcnow()
        await session.commit()
    return op_session
