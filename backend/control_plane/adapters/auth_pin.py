"""PIN login and PIN management endpoints."""

from __future__ import annotations

from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.auth_cookies import clear_auth_cookie, set_auth_cookie
from backend.control_plane.adapters.auth_session_projection import build_authenticated_session_response
from backend.control_plane.adapters.auth_shared import (
    assert_user_active,
    build_auth_actor_payload,
    hash_pin,
    register_login_session,
    request_tenant_id,
    resolve_auth_actor,
)
from backend.control_plane.adapters.auth_token_issue import issue_auth_token
from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.deps import get_current_user, get_db, get_redis, get_tenant_db
from backend.control_plane.adapters.models.auth import AuthSessionResponse, PinLoginRequest, PinSetRequest
from backend.control_plane.auth.auth_helpers import (
    CODE_BAD_REQUEST,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_TOO_MANY,
    CODE_UNAUTHORIZED,
    client_ip,
    is_private_ip,
    log_auth,
    request_id,
    require_db_redis,
    zen,
)
from backend.control_plane.auth.sessions import revoke_all_user_sessions
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.user import User
from backend.platform.logging.audit import log_audit
from backend.platform.redis.client import CHANNEL_USER_EVENTS, RedisClient

router = APIRouter()

PIN_RATE_LIMIT_KEY = "pin:rate:"
PIN_RATE_LIMIT_MAX = 5
PIN_RATE_LIMIT_WINDOW = 900  # Lock the client IP for 15 minutes after repeated failures.


def _pin_lockout_window_text() -> str:
    minutes, seconds = divmod(PIN_RATE_LIMIT_WINDOW, 60)
    if minutes and seconds:
        return f"{minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _clear_auth_cookie_after_pin_mutation(response: Response) -> None:
    clear_auth_cookie(response)


async def _record_pin_mutation_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    current_user: dict[str, object],
    user: User,
    had_existing_pin: bool,
    revoked_sessions: int,
) -> None:
    actor = resolve_auth_actor(current_user)
    await log_audit(
        db,
        tenant_id=tenant_id,
        action="auth.pin.updated",
        result="success",
        user_id=actor.user_id,
        username=actor.username,
        resource_type="user",
        resource_id=str(user.id),
        details={
            "target_user_id": str(user.id),
            "target_username": user.username,
            "had_existing_pin": had_existing_pin,
            "revoked_sessions": revoked_sessions,
        },
    )


async def _publish_pin_mutation_event(
    *,
    tenant_id: str,
    current_user: dict[str, object],
    user: User,
    had_existing_pin: bool,
    revoked_sessions: int,
) -> None:
    await publish_control_event(
        CHANNEL_USER_EVENTS,
        "pin_updated",
        {
            "target_user_id": str(user.id),
            "user": {
                "id": str(user.id),
                "username": user.username,
            },
            "had_existing_pin": had_existing_pin,
            "revoked_sessions": revoked_sessions,
            "actor": build_auth_actor_payload(current_user),
        },
        tenant_id=tenant_id,
    )


@router.post("/pin/login", response_model=AuthSessionResponse)
async def pin_login(
    req: PinLoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession | None, Depends(get_db)],
    redis: Annotated[RedisClient, Depends(get_redis)],
) -> AuthSessionResponse:
    require_db_redis(db, redis)
    assert db is not None  # noqa: S101
    rid, cip = request_id(request), client_ip(request)
    tenant_id = request_tenant_id(req.tenant_id)

    if not is_private_ip(cip):
        log_auth("pin_login", False, rid, username=req.username, client_ip_str=cip, detail="not_private_ip")
        raise zen(CODE_FORBIDDEN, "PIN login only allowed from local network", status.HTTP_403_FORBIDDEN)

    freeze_key = f"pin:freeze:{cip}"
    if await redis.kv.get(freeze_key):
        raise zen(
            CODE_TOO_MANY,
            f"Too many failed PIN attempts; locked for {_pin_lockout_window_text()}",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == req.username))
    user = result.scalar_one_or_none()

    async def _handle_failure(detail: str) -> None:
        rate_key = f"{PIN_RATE_LIMIT_KEY}{cip}"
        count = await redis.kv.incr(rate_key)
        if count == 1:
            await redis.kv.expire(rate_key, PIN_RATE_LIMIT_WINDOW)
        if count >= PIN_RATE_LIMIT_MAX:
            await redis.kv.setex(freeze_key, PIN_RATE_LIMIT_WINDOW, "1")
            log_auth("pin_login", False, rid, username=req.username, client_ip_str=cip, detail="trigger_lock")
            raise zen(
                CODE_TOO_MANY,
                f"Too many failed PIN attempts; locked for {_pin_lockout_window_text()}",
                status.HTTP_429_TOO_MANY_REQUESTS,
            )
        log_auth("pin_login", False, rid, username=req.username, client_ip_str=cip, detail=detail)
        raise zen(CODE_UNAUTHORIZED, "Invalid credentials", status.HTTP_401_UNAUTHORIZED)

    if not user or not user.pin_hash:
        await _handle_failure("invalid_user_or_no_pin")

    assert user is not None and user.pin_hash is not None  # noqa: S101
    assert_user_active(user, flow="pin_login", rid=rid, username=req.username, client_ip_str=cip)
    pin_bytes = req.pin.encode("utf-8")
    pin_hash_bytes = user.pin_hash.encode("utf-8") if isinstance(user.pin_hash, str) else user.pin_hash
    if not bcrypt.checkpw(pin_bytes, pin_hash_bytes):
        await _handle_failure("wrong_pin")

    await redis.kv.delete(f"{PIN_RATE_LIMIT_KEY}{cip}")
    await redis.kv.delete(freeze_key)

    log_auth("pin_login", True, rid, username=req.username, client_ip_str=cip)
    from backend.control_plane.auth.permissions import get_user_scopes, hydrate_scopes_for_role

    user_scopes = hydrate_scopes_for_role(
        await get_user_scopes(db, tenant_id=user.tenant_id, user_id=str(user.id)),
        user.role,
    )
    issued_token = issue_auth_token(
        str(user.id),
        user.username,
        user.role,
        tenant_id=user.tenant_id,
        ai_route_preference=user.ai_route_preference or "auto",
        scopes=user_scopes,
    )
    await register_login_session(
        db,
        tenant_id=user.tenant_id,
        user_id=str(user.id),
        username=user.username,
        session_id=issued_token.session_id,
        token_id=issued_token.token_id,
        ip_address=cip,
        user_agent=request.headers.get("user-agent"),
        auth_method="pin",
        redis=redis,
    )
    set_auth_cookie(response, issued_token.access_token)
    return build_authenticated_session_response(
        sub=str(user.id),
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        ai_route_preference=user.ai_route_preference or "auto",
        scopes=user_scopes,
        expires_in=issued_token.expires_in,
    )


@router.post("/pin/set")
async def pin_set(
    req: PinSetRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    current_user: Annotated[dict[str, object], Depends(get_current_user)],
) -> dict[str, str]:
    """Set or rotate the current user's PIN after validating ownership."""
    rid, cip = request_id(request), client_ip(request)
    username = str(current_user.get("username") or "").strip()
    tenant_id = require_current_user_tenant_id(current_user)
    if not username:
        raise zen(CODE_UNAUTHORIZED, "Invalid token payload", status.HTTP_401_UNAUTHORIZED)

    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)

    had_existing_pin = bool(user.pin_hash)
    if had_existing_pin:
        if not req.pin_old:
            raise zen(CODE_BAD_REQUEST, "pin_old required when changing PIN", status.HTTP_400_BAD_REQUEST)
        pin_old_bytes = req.pin_old.encode("utf-8")
        hash_bytes = user.pin_hash.encode("utf-8") if isinstance(user.pin_hash, str) else user.pin_hash
        if hash_bytes is None:
            raise zen(CODE_FORBIDDEN, "PIN verification is unavailable", status.HTTP_403_FORBIDDEN)
        if not bcrypt.checkpw(pin_old_bytes, hash_bytes):
            log_auth("pin_set", False, rid, username=username, client_ip_str=cip, detail="wrong_pin_old")
            raise zen(CODE_UNAUTHORIZED, "Invalid pin_old", status.HTTP_401_UNAUTHORIZED)

    user.pin_hash = hash_pin(req.pin_new)
    revoked_sessions = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=str(user.id),
        revoked_by=f"user:pin_change:{username}",
        redis=None,
    )
    await _record_pin_mutation_audit(
        db,
        tenant_id=tenant_id,
        current_user=current_user,
        user=user,
        had_existing_pin=had_existing_pin,
        revoked_sessions=revoked_sessions,
    )
    await db.commit()
    _clear_auth_cookie_after_pin_mutation(response)
    await _publish_pin_mutation_event(
        tenant_id=tenant_id,
        current_user=current_user,
        user=user,
        had_existing_pin=had_existing_pin,
        revoked_sessions=revoked_sessions,
    )
    log_auth("pin_set", True, rid, username=username, client_ip_str=cip)
    return {"status": "ok", "message": "PIN updated"}
