"""Session management API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_admin, get_current_user, get_redis, get_tenant_db
from backend.control_plane.auth.sessions import list_user_sessions, revoke_all_user_sessions, revoke_session
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.session import Session
from backend.platform.redis.client import RedisClient

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    username: str
    device_name: str | None
    ip_address: str | None
    auth_method: str
    is_active: bool
    created_at: str
    last_seen_at: str
    expires_at: str
    revoked_at: str | None
    revoked_by: str | None


def _to_response(s: Session) -> SessionResponse:
    return SessionResponse(
        session_id=s.session_id,
        user_id=s.user_id,
        username=s.username,
        device_name=s.device_name,
        ip_address=s.ip_address,
        auth_method=s.auth_method,
        is_active=s.is_active,
        created_at=s.created_at.isoformat(),
        last_seen_at=s.last_seen_at.isoformat(),
        expires_at=s.expires_at.isoformat(),
        revoked_at=s.revoked_at.isoformat() if s.revoked_at else None,
        revoked_by=s.revoked_by,
    )


@router.get("/me", response_model=list[SessionResponse])
async def list_my_sessions(
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[SessionResponse]:
    """List all active sessions for the current user."""
    tenant_id = require_current_user_tenant_id(current_user)
    sessions = await list_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=current_user["sub"],
    )
    return [_to_response(s) for s in sessions]


@router.delete("/me/{session_id}")
async def revoke_my_session(
    session_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> dict[str, str]:
    """Revoke one of the current user's sessions."""
    tenant_id = require_current_user_tenant_id(current_user)
    await revoke_session(
        db,
        session_id,
        tenant_id=tenant_id,
        revoked_by=current_user["username"],
        redis=redis,
    )
    return {"status": "ok", "message": "Session revoked"}


@router.delete("/me")
async def revoke_all_my_sessions(
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> dict[str, object]:
    """Revoke all sessions for the current user (logout everywhere)."""
    tenant_id = require_current_user_tenant_id(current_user)
    count = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=current_user["sub"],
        revoked_by=current_user["username"],
        redis=redis,
    )
    return {"status": "ok", "revoked": count}


@router.get("/users/{user_id}", response_model=list[SessionResponse])
async def list_user_sessions_admin(
    user_id: str,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[SessionResponse]:
    """List sessions for any user (admin only)."""
    tenant_id = require_current_user_tenant_id(current_user)
    sessions = await list_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        include_expired=True,
    )
    return [_to_response(s) for s in sessions]


@router.delete("/users/{user_id}")
async def revoke_all_user_sessions_admin(
    user_id: str,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> dict[str, object]:
    """Revoke all sessions for a user (admin only)."""
    tenant_id = require_current_user_tenant_id(current_user)
    count = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        revoked_by=current_user["username"],
        redis=redis,
    )
    return {"status": "ok", "revoked": count}
