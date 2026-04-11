"""Session management API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.auth_cookies import clear_auth_cookie
from backend.control_plane.adapters.auth_shared import (
    build_auth_actor_payload,
    require_auth_user_id,
    require_auth_username,
    resolve_auth_actor,
    should_clear_auth_cookie_for_self_target,
)
from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.deps import get_current_admin, get_current_user, get_redis, get_tenant_db
from backend.control_plane.auth.sessions import list_user_sessions, revoke_all_user_sessions, revoke_owned_session
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.session import Session
from backend.platform.logging.audit import log_audit
from backend.platform.redis.client import CHANNEL_SESSION_EVENTS, RedisClient

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


def _clear_auth_cookie_for_self_session_mutation(
    response: Response,
    *,
    current_user: dict[str, object],
    target_user_id: str,
    revoked_session_id: str | None = None,
) -> None:
    if should_clear_auth_cookie_for_self_target(
        current_user,
        target_user_id=target_user_id,
        target_session_id=revoked_session_id,
    ):
        clear_auth_cookie(response)


async def _record_session_mutation_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    action: str,
    current_user: dict[str, object],
    target_user_id: str,
    revoked_sessions: int,
    current_session_affected: bool,
    session_id: str | None = None,
    target_username: str | None = None,
) -> None:
    actor = resolve_auth_actor(current_user)
    details = {
        "target_user_id": str(target_user_id).strip(),
        "revoked_sessions": revoked_sessions,
        "current_session_affected": current_session_affected,
    }
    if target_username:
        details["target_username"] = target_username
    if session_id:
        details["session_id"] = session_id
    await log_audit(
        db,
        tenant_id=tenant_id,
        action=action,
        result="success",
        user_id=actor.user_id,
        username=actor.username,
        resource_type="session" if session_id else "user_sessions",
        resource_id=session_id or str(target_user_id).strip(),
        details=details,
    )


async def _publish_session_mutation_event(
    action: str,
    *,
    tenant_id: str,
    current_user: dict[str, object],
    target_user_id: str,
    revoked_sessions: int,
    current_session_affected: bool,
    session_id: str | None = None,
    target_username: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "target_user_id": str(target_user_id).strip(),
        "revoked_sessions": revoked_sessions,
        "current_session_affected": current_session_affected,
        "actor": build_auth_actor_payload(current_user),
    }
    if session_id:
        payload["session"] = {
            "id": session_id,
            "username": target_username,
        }
    else:
        payload["user"] = {
            "id": str(target_user_id).strip(),
            "username": target_username,
        }
    await publish_control_event(CHANNEL_SESSION_EVENTS, action, payload, tenant_id=tenant_id)


@router.get("/me", response_model=list[SessionResponse])
async def list_my_sessions(
    current_user: Annotated[dict[str, object], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[SessionResponse]:
    """List all active sessions for the current user."""
    tenant_id = require_current_user_tenant_id(current_user)
    sessions = await list_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=require_auth_user_id(current_user),
    )
    return [_to_response(s) for s in sessions]


@router.delete("/me/{session_id}")
async def revoke_my_session(
    session_id: str,
    response: Response,
    current_user: Annotated[dict[str, object], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> dict[str, str]:
    """Revoke one of the current user's sessions."""
    tenant_id = require_current_user_tenant_id(current_user)
    actor = resolve_auth_actor(current_user)
    session = await revoke_owned_session(
        db,
        session_id,
        tenant_id=tenant_id,
        user_id=require_auth_user_id(current_user),
        revoked_by=require_auth_username(current_user),
        redis=redis,
    )
    current_session_affected = actor.session_id == str(session_id).strip()
    await _record_session_mutation_audit(
        db,
        tenant_id=tenant_id,
        action="auth.session.revoked",
        current_user=current_user,
        target_user_id=str(session.user_id),
        target_username=session.username,
        revoked_sessions=1,
        current_session_affected=current_session_affected,
        session_id=session.session_id,
    )
    await db.commit()
    _clear_auth_cookie_for_self_session_mutation(
        response,
        current_user=current_user,
        target_user_id=str(session.user_id),
        revoked_session_id=session.session_id,
    )
    await _publish_session_mutation_event(
        "session_revoked",
        tenant_id=tenant_id,
        current_user=current_user,
        target_user_id=str(session.user_id),
        target_username=session.username,
        revoked_sessions=1,
        current_session_affected=current_session_affected,
        session_id=session.session_id,
    )
    return {"status": "ok", "message": "Session revoked"}


@router.delete("/me")
async def revoke_all_my_sessions(
    response: Response,
    current_user: Annotated[dict[str, object], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> dict[str, object]:
    """Revoke all sessions for the current user (logout everywhere)."""
    tenant_id = require_current_user_tenant_id(current_user)
    count = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=require_auth_user_id(current_user),
        revoked_by=require_auth_username(current_user),
        redis=redis,
    )
    await _record_session_mutation_audit(
        db,
        tenant_id=tenant_id,
        action="auth.session.revoked_all",
        current_user=current_user,
        target_user_id=str(current_user["sub"]),
        target_username=str(current_user.get("username") or "") or None,
        revoked_sessions=count,
        current_session_affected=True,
    )
    await db.commit()
    _clear_auth_cookie_for_self_session_mutation(
        response,
        current_user=current_user,
        target_user_id=str(current_user["sub"]),
    )
    await _publish_session_mutation_event(
        "sessions_revoked",
        tenant_id=tenant_id,
        current_user=current_user,
        target_user_id=str(current_user["sub"]),
        target_username=str(current_user.get("username") or "") or None,
        revoked_sessions=count,
        current_session_affected=True,
    )
    return {"status": "ok", "revoked": count}


@router.get("/users/{user_id}", response_model=list[SessionResponse])
async def list_user_sessions_admin(
    user_id: str,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
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
    response: Response,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> dict[str, object]:
    """Revoke all sessions for a user (admin only)."""
    tenant_id = require_current_user_tenant_id(current_user)
    actor = resolve_auth_actor(current_user)
    count = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        revoked_by=require_auth_username(current_user),
        redis=redis,
    )
    current_session_affected = str(user_id).strip() == (actor.user_id or "")
    await _record_session_mutation_audit(
        db,
        tenant_id=tenant_id,
        action="auth.session.user_revoked_all",
        current_user=current_user,
        target_user_id=user_id,
        revoked_sessions=count,
        current_session_affected=current_session_affected,
    )
    await db.commit()
    _clear_auth_cookie_for_self_session_mutation(
        response,
        current_user=current_user,
        target_user_id=user_id,
    )
    await _publish_session_mutation_event(
        "user_sessions_revoked",
        tenant_id=tenant_id,
        current_user=current_user,
        target_user_id=user_id,
        revoked_sessions=count,
        current_session_affected=current_session_affected,
    )
    return {"status": "ok", "revoked": count}
