"""
ZEN70 Auth PIN - PIN 降级认证与设置
"""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth_shared import assert_user_active, build_token_response_model, hash_pin, register_login_session, request_tenant_id
from backend.api.deps import get_current_user, get_db, get_redis
from backend.api.models.auth import PinLoginRequest, PinSetRequest, TokenResponse
from backend.core.auth_helpers import (
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
from backend.core.redis_client import RedisClient
from backend.models.user import User

router = APIRouter()

PIN_RATE_LIMIT_KEY = "pin:rate:"
PIN_RATE_LIMIT_MAX = 5
PIN_RATE_LIMIT_WINDOW = 900  # 法典 3.6：5 次错误锁定 IP 15 分钟


def _pin_lockout_window_text() -> str:
    minutes, seconds = divmod(PIN_RATE_LIMIT_WINDOW, 60)
    if minutes and seconds:
        return f"{minutes} 分 {seconds} 秒"
    if minutes:
        return f"{minutes} 分钟"
    return f"{seconds} 秒"


@router.post("/pin/login", response_model=TokenResponse)
async def pin_login(
    req: PinLoginRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> TokenResponse:
    require_db_redis(db, redis)
    assert db is not None  # noqa: S101
    rid, cip = request_id(request), client_ip(request)
    tenant_id = request_tenant_id(req.tenant_id)

    if not is_private_ip(cip):
        log_auth("pin_login", False, rid, username=req.username, client_ip_str=cip, detail="not_private_ip")
        raise zen(CODE_FORBIDDEN, "PIN login only allowed from local network", status.HTTP_403_FORBIDDEN)

    freeze_key = f"pin:freeze:{cip}"
    if await redis.get(freeze_key):
        raise zen(CODE_TOO_MANY, f"错误次数过多，已被防爆破大闸冻结 {_pin_lockout_window_text()}", status.HTTP_429_TOO_MANY_REQUESTS)

    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == req.username))
    user = result.scalar_one_or_none()

    async def _handle_failure(detail: str) -> None:
        count = await redis.incr_with_expire(f"{PIN_RATE_LIMIT_KEY}{cip}", PIN_RATE_LIMIT_WINDOW)
        if count >= PIN_RATE_LIMIT_MAX:
            await redis.setex(freeze_key, PIN_RATE_LIMIT_WINDOW, "1")
            log_auth("pin_login", False, rid, username=req.username, client_ip_str=cip, detail="trigger_lock")
            raise zen(CODE_TOO_MANY, f"连续失败 {PIN_RATE_LIMIT_MAX} 次，已触发 {_pin_lockout_window_text()} 锁定防爆破", status.HTTP_429_TOO_MANY_REQUESTS)
        log_auth("pin_login", False, rid, username=req.username, client_ip_str=cip, detail=detail)
        raise zen(CODE_UNAUTHORIZED, "Invalid credentials", status.HTTP_401_UNAUTHORIZED)

    if not user or not user.pin_hash:
        await _handle_failure("invalid_user_or_no_pin")
        return build_token_response_model("0", "", "")  # unreachable

    assert user is not None and user.pin_hash is not None  # noqa: S101
    assert_user_active(user, flow="pin_login", rid=rid, username=req.username, client_ip_str=cip)
    pin_bytes = req.pin.encode("utf-8")
    pin_hash_bytes = user.pin_hash.encode("utf-8") if isinstance(user.pin_hash, str) else user.pin_hash
    if not bcrypt.checkpw(pin_bytes, pin_hash_bytes):
        await _handle_failure("wrong_pin")
        return build_token_response_model("0", "", "")  # unreachable

    await redis.delete(f"{PIN_RATE_LIMIT_KEY}{cip}")
    await redis.delete(freeze_key)

    log_auth("pin_login", True, rid, username=req.username, client_ip_str=cip)
    resp = build_token_response_model(
        str(user.id),
        user.username,
        user.role,
        tenant_id=user.tenant_id,
        ai_route_preference=user.ai_route_preference or "auto",
    )
    await register_login_session(
        db,
        tenant_id=user.tenant_id,
        user_id=str(user.id),
        username=user.username,
        access_token=resp.access_token,
        ip_address=cip,
        user_agent=request.headers.get("user-agent"),
        auth_method="pin",
    )
    return resp


@router.post("/pin/set")
async def pin_set(
    req: PinSetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, str]:
    """设置或修改当前用户 PIN（需已登录；若账户已有 PIN 则需提供 pin_old）。"""
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
