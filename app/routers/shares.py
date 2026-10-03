"""Manager-issued, time-limited public location access."""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.auth import ManagerUser
from app.db import SessionDep, utcnow
from app.models import Machine, MachineShare
from app.routers.machines import get_org_machine
from app.schemas.machine_share import MachineShareIssued, SharedMachineLocation

router = APIRouter(tags=["machine shares"])
SHARE_LIFETIME = timedelta(hours=24)


def _prevent_caching(response: Response) -> None:
    now = utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    response.headers["Cache-Control"] = "no-cache, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = now
    response.headers["Date"] = now


@router.post(
    "/machines/{machine_id}/shares",
    response_model=MachineShareIssued,
    status_code=status.HTTP_201_CREATED,
)
async def create_machine_share(
    machine_id: uuid.UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    user: ManagerUser,
) -> MachineShareIssued:
    _prevent_caching(response)
    machine = await get_org_machine(session, user, machine_id)
    if not machine.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")

    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + SHARE_LIFETIME
    session.add(
        MachineShare(
            machine_id=machine.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=expires_at,
        )
    )
    await session.commit()
    return MachineShareIssued(
        url=f"{request.url_for('shared_machine_page')}#token={token}",
        expires_at=expires_at,
    )


@router.get("/shares", response_class=HTMLResponse)
async def shared_machine_page(response: Response) -> str:
    _prevent_caching(response)
    response.headers["Referrer-Policy"] = "no-referrer"
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Machine location</title></head><body>
<h1>Machine location</h1><p id="status">Loading location…</p><p id="position"></p><p id="seen"></p>
<script>
const token = new URLSearchParams(location.hash.slice(1)).get('token');
async function refresh() {
  if (!token) { document.getElementById('status').textContent = 'Invalid share link'; return; }
  try {
    const response = await fetch('shares/location', {headers: {'X-Share-Token': token}, cache: 'no-store'});
    if (!response.ok) {
      document.getElementById('status').textContent = 'Share link expired or unavailable';
      document.getElementById('position').textContent = '';
      document.getElementById('seen').textContent = '';
      return;
    }
    const data = await response.json();
    document.getElementById('status').textContent = 'Latest reported position';
    document.getElementById('position').textContent = data.latitude == null
      ? 'No position reported yet' : `${data.latitude}, ${data.longitude}`;
    document.getElementById('seen').textContent = data.last_seen_at
      ? `Last seen: ${new Date(data.last_seen_at).toLocaleString()}` : '';
  } catch {
    document.getElementById('status').textContent = 'Location temporarily unavailable';
  }
}
refresh();
setInterval(refresh, 15000);
</script></body></html>"""


@router.get("/shares/location", response_model=SharedMachineLocation)
async def get_shared_machine_location(
    session: SessionDep,
    response: Response,
    token: Annotated[str | None, Header(alias="X-Share-Token")] = None,
) -> SharedMachineLocation:
    _prevent_caching(response)
    if not token:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share link not found")
    share = await session.scalar(
        select(MachineShare).where(
            MachineShare.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    if share is None or share.expires_at <= utcnow():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share link not found")

    machine = await session.get(Machine, share.machine_id)
    if machine is None or not machine.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share link not found")
    return SharedMachineLocation(
        machine_id=machine.id,
        latitude=machine.last_latitude,
        longitude=machine.last_longitude,
        last_seen_at=machine.last_seen_at,
    )
