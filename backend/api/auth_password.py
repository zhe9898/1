"""
ZEN70 Auth Password - 瀵嗙爜璁よ瘉
"""

from __future__ import annotations

import asyncio

import bcrypt
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth_cookies import set_auth_cookie
from backend.api.auth_session_projection import build_authenticated_session_response
from backend.api.auth_shared import assert_user_active, register_login_session, request_tenant_id
from backend.api.auth_token_issue import issue_auth_token
from backend.api.deps import get_db, get_redis
from backend.api.models.auth import AuthSessionResponse, PasswordLoginRequest
from backend.control_plane.auth.auth_helpers import (
    CODE_BAD_REQUEST,
    CODE_TOO_MANY,
    CODE_UNAUTHORIZED,
    client_ip,
    log_auth,
    request_id,
    require_db_redis,
    zen,
)
from backend.models.user import User
from backend.platform.redis.client import RedisClient

router = APIRouter()

# A pre-hashed sentinel used for constant-time password comparison when the
# target user does not exist, preventing username enumeration via response timing.
_DUMMY_HASH: bytes = bcrypt.hashpw(b"zen70-dummy-constant-time-sentinel", bcrypt.gensalt(rounds=12))


def _coerce_password_hash_bytes(password_hash: object) -> bytes | None:
    if isinstance(password_hash, bytes):
        return password_hash
    if isinstance(password_hash, str):
        try:
            return password_hash.encode("utf-8")
        except UnicodeEncodeError:
            return None
    return None


@router.post("/password/login", response_model=AuthSessionResponse)
async def password_login(
    req: PasswordLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> AuthSessionResponse:
    """Sanitized legacy docstring."""
    require_db_redis(db, redis)
    assert db is not None  # noqa: S101
    rid, cip = request_id(request), client_ip(request)
    tenant_id = request_tenant_id(req.tenant_id)
    username = req.username.strip()
    if not username:
        raise zen(CODE_BAD_REQUEST, "Username cannot be empty", status.HTTP_400_BAD_REQUEST)
    limit_key = f"pwd:rate:{cip}"
    lock_key = f"pwd:lock:{cip}"

    if await redis.kv.get(lock_key):
        log_auth("password_login", False, rid, username=username, client_ip_str=cip, detail="hard_locked")
        raise zen(CODE_TOO_MANY, "IP is temporarily locked for 15 minutes", status.HTTP_429_TOO_MANY_REQUESTS)

    count_str = await redis.kv.get(limit_key)
    fail_count = int(count_str) if count_str else 0

    if fail_count == 1:
        await asyncio.sleep(1)
    elif fail_count == 2:
        await asyncio.sleep(5)
    elif fail_count >= 3:
        await asyncio.sleep(30)

    from backend.api.auth_shared import first_user_or_schema_unavailable

    await first_user_or_schema_unavailable(db)
    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == username))
    user = result.scalar_one_or_none()

    is_valid = False
    candidate_password = req.password.encode("utf-8")
    if user and user.password_hash:
        stored_hash = _coerce_password_hash_bytes(user.password_hash)
        if stored_hash is not None:
            try:
                is_valid = bcrypt.checkpw(candidate_password, stored_hash)
            except ValueError:
                is_valid = False
        if not is_valid:
            bcrypt.checkpw(candidate_password, _DUMMY_HASH)
    else:
        # Always run bcrypt to maintain constant response time regardless of whether
        # the username exists, preventing user enumeration via timing side-channel.
        bcrypt.checkpw(candidate_password, _DUMMY_HASH)

    if not is_valid:
        new_count = await redis.kv.incr(limit_key)
        if new_count == 1:
            await redis.kv.expire(limit_key, 900)
        if new_count >= 5:
            await redis.kv.setex(lock_key, 900, "1")
            log_auth("password_login", False, rid, username=username, client_ip_str=cip, detail="trigger_lock")
            raise zen(CODE_TOO_MANY, "杩炵画澶辫触 5 娆★紝IP 宸茶閿佸畾 15 鍒嗛挓", status.HTTP_429_TOO_MANY_REQUESTS)
        log_auth("password_login", False, rid, username=username, client_ip_str=cip, detail="wrong_password_or_user")
        raise zen(CODE_UNAUTHORIZED, "Invalid credentials", status.HTTP_401_UNAUTHORIZED)

    assert user is not None  # noqa: S101
    assert_user_active(user, flow="password_login", rid=rid, username=username, client_ip_str=cip)
    await redis.kv.delete(limit_key)
    await redis.kv.delete(lock_key)

    log_auth("password_login", True, rid, username=username, client_ip_str=cip)

    # Load user scopes from permissions table for JWT
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
        ai_route_preference=user.ai_route_preference,
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
        auth_method="password",
    )
    set_auth_cookie(response, issued_token.access_token)
    return build_authenticated_session_response(
        sub=str(user.id),
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        ai_route_preference=user.ai_route_preference,
        scopes=user_scopes,
        expires_in=issued_token.expires_in,
    )
