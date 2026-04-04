"""
ZEN70 Auth Password - 密码认证
"""

from __future__ import annotations

import asyncio

import bcrypt
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth_shared import assert_user_active, build_token_response_model, register_login_session, request_tenant_id
from backend.api.deps import get_db, get_redis
from backend.api.models.auth import PasswordLoginRequest, TokenResponse
from backend.core.auth_helpers import (
    CODE_BAD_REQUEST,
    CODE_TOO_MANY,
    CODE_UNAUTHORIZED,
    client_ip,
    log_auth,
    request_id,
    require_db_redis,
    zen,
)
from backend.core.redis_client import RedisClient
from backend.models.user import User

router = APIRouter()


@router.post("/password/login", response_model=TokenResponse)
async def password_login(
    req: PasswordLoginRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> TokenResponse:
    """标准密码登录通道，防爆破，依赖 tenant 和 role。"""
    require_db_redis(db, redis)
    assert db is not None  # noqa: S101
    rid, cip = request_id(request), client_ip(request)
    tenant_id = request_tenant_id(req.tenant_id)
    username = req.username.strip()
    if not username:
        raise zen(CODE_BAD_REQUEST, "Username cannot be empty", status.HTTP_400_BAD_REQUEST)
    limit_key = f"pwd:rate:{cip}"
    lock_key = f"pwd:lock:{cip}"

    if await redis.get(lock_key):
        log_auth("password_login", False, rid, username=username, client_ip_str=cip, detail="hard_locked")
        raise zen(CODE_TOO_MANY, "IP 已被强制锁定，请 15 分钟后再试或联系指挥官解锁", status.HTTP_429_TOO_MANY_REQUESTS)

    count_str = await redis.get(limit_key)
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
    if user and user.password_hash:
        if bcrypt.checkpw(req.password.encode("utf-8"), user.password_hash.encode("utf-8")):
            is_valid = True

    if not is_valid:
        new_count = await redis.incr(limit_key)
        if new_count == 1:
            await redis.expire(limit_key, 900)
        if new_count >= 5:
            await redis.setex(lock_key, 900, "1")
            log_auth("password_login", False, rid, username=username, client_ip_str=cip, detail="trigger_lock")
            raise zen(CODE_TOO_MANY, "连续失败 5 次，IP 已被锁定 15 分钟", status.HTTP_429_TOO_MANY_REQUESTS)
        log_auth("password_login", False, rid, username=username, client_ip_str=cip, detail="wrong_password_or_user")
        raise zen(CODE_UNAUTHORIZED, "Invalid credentials", status.HTTP_401_UNAUTHORIZED)

    assert user is not None  # noqa: S101
    assert_user_active(user, flow="password_login", rid=rid, username=username, client_ip_str=cip)
    await redis.delete(limit_key)
    await redis.delete(lock_key)

    log_auth("password_login", True, rid, username=username, client_ip_str=cip)

    # Load user scopes from permissions table for JWT
    from backend.core.permissions import get_user_scopes

    user_scopes = await get_user_scopes(db, tenant_id=user.tenant_id, user_id=str(user.id))

    resp = build_token_response_model(
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
        access_token=resp.access_token,
        ip_address=cip,
        user_agent=request.headers.get("user-agent"),
        auth_method="password",
    )
    return resp
