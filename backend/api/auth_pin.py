"""
ZEN70 Auth PIN - PIN 闄嶇骇璁よ瘉涓庤缃?"""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth_cookies import set_auth_cookie
from backend.api.auth_session_projection import build_authenticated_session_response
from backend.api.auth_shared import assert_user_active, hash_pin, register_login_session, request_tenant_id
from backend.api.auth_token_issue import issue_auth_token
from backend.api.deps import get_current_user, get_db, get_redis
from backend.api.models.auth import AuthSessionResponse, PinLoginRequest, PinSetRequest
from backend.control_plane.auth.auth_helpers import (
    CODE_BAD_REQUEST,
    CODE_DB_UNAVAILABLE,
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
from backend.models.user import User
from backend.platform.redis.client import RedisClient

router = APIRouter()

PIN_RATE_LIMIT_KEY = "pin:rate:"
PIN_RATE_LIMIT_MAX = 5
PIN_RATE_LIMIT_WINDOW = 900  # 娉曞吀 3.6锛? 娆￠敊璇攣瀹?IP 15 鍒嗛挓


def _pin_lockout_window_text() -> str:
    minutes, seconds = divmod(PIN_RATE_LIMIT_WINDOW, 60)
    if minutes and seconds:
        return f"{minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


@router.post("/pin/login", response_model=AuthSessionResponse)
async def pin_login(
    req: PinLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
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
        raise zen(CODE_TOO_MANY, f"閿欒娆℃暟杩囧锛屽凡琚槻鐖嗙牬澶ч椄鍐荤粨 {_pin_lockout_window_text()}", status.HTTP_429_TOO_MANY_REQUESTS)

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
        access_token=issued_token.access_token,
        ip_address=cip,
        user_agent=request.headers.get("user-agent"),
        auth_method="pin",
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
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, str]:
    """Sanitized legacy docstring."""
    if db is None:
        raise zen(CODE_DB_UNAVAILABLE, "Database not configured", status.HTTP_503_SERVICE_UNAVAILABLE)
    rid, cip = request_id(request), client_ip(request)
    username = current_user.get("username")
    tenant_id = str(current_user.get("tenant_id") or "default")
    if not username:
        raise zen(CODE_UNAUTHORIZED, "Invalid token payload", status.HTTP_401_UNAUTHORIZED)

    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)

    if user.pin_hash:
        if not req.pin_old:
            raise zen(CODE_BAD_REQUEST, "pin_old required when changing PIN", status.HTTP_400_BAD_REQUEST)
        pin_old_bytes = req.pin_old.encode("utf-8")
        hash_bytes = user.pin_hash.encode("utf-8") if isinstance(user.pin_hash, str) else user.pin_hash
        if not bcrypt.checkpw(pin_old_bytes, hash_bytes):
            log_auth("pin_set", False, rid, username=username, client_ip_str=cip, detail="wrong_pin_old")
            raise zen(CODE_UNAUTHORIZED, "Invalid pin_old", status.HTTP_401_UNAUTHORIZED)

    user.pin_hash = hash_pin(req.pin_new)
    log_auth("pin_set", True, rid, username=username, client_ip_str=cip)
    return {"status": "ok", "message": "PIN updated"}
