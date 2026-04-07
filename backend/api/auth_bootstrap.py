"""
ZEN70 Auth Bootstrap - 系统初始化（首次运行）
"""

from __future__ import annotations

import bcrypt
import logging
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth_cookies import set_auth_cookie
from backend.api.deps import get_db, get_redis
from backend.api.models.auth import BootstrapRequest, TokenResponse
from backend.core.auth_helpers import (
    CODE_DB_UNAVAILABLE,
    CODE_FORBIDDEN,
    require_db_redis,
    token_response,
    zen,
)
from backend.core.redis_client import RedisClient
from backend.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

BCRYPT_ROUNDS = 12
BOOTSTRAP_LOCK_KEY = "auth:bootstrap:lock"
BOOTSTRAP_LOCK_TTL_SECONDS = 60


def _build_token_response_model(
    sub: str,
    username: str,
    role: str = "user",
    *,
    tenant_id: str = "default",
    ai_route_preference: str = "auto",
    scopes: list[str] | None = None,
) -> TokenResponse:
    body = token_response(sub, username, role, tenant_id=tenant_id, ai_route_preference=ai_route_preference, scopes=scopes)
    return TokenResponse(
        access_token=str(body["access_token"]),
        token_type=str(body["token_type"]),
        expires_in=int(body["expires_in"]),
    )


@router.get("/sys/status")
async def sys_status(db: AsyncSession | None = Depends(get_db)) -> dict[str, bool]:
    """检查数据库是否有用户。"""
    if db is None:
        raise zen(CODE_DB_UNAVAILABLE, "DB unavailable", status.HTTP_503_SERVICE_UNAVAILABLE)
    from backend.api.auth_shared import first_user_or_schema_unavailable

    has_user = (await first_user_or_schema_unavailable(db)) is not None
    return {"initialized": has_user}


@router.post("/bootstrap", response_model=TokenResponse)
async def bootstrap(
    req: BootstrapRequest,
    response: Response,
    db: AsyncSession | None = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> TokenResponse:
    """初始化第一个管理员账户。只有在库为空时可用。"""
    require_db_redis(db, redis)
    assert db is not None  # noqa: S101
    from backend.api.auth_shared import first_user_or_schema_unavailable

    locked = await redis.acquire_lock(BOOTSTRAP_LOCK_KEY, ttl=BOOTSTRAP_LOCK_TTL_SECONDS)
    if not locked:
        raise zen(
            CODE_FORBIDDEN,
            "Bootstrap already in progress",
            status.HTTP_403_FORBIDDEN,
        )
    try:
        first_user = await first_user_or_schema_unavailable(db)
        if first_user is not None:
            raise zen(CODE_FORBIDDEN, "System already initialized", status.HTTP_403_FORBIDDEN)

        hashed_pw = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")
        user = User(
            username=req.username,
            display_name=req.display_name,
            role="admin",
            password_hash=hashed_pw,
            tenant_id="admin_tenant",
        )
        db.add(user)
        await db.flush()
        await db.commit()
        from backend.core.permissions import hydrate_scopes_for_role

        token_model = _build_token_response_model(
            str(user.id),
            user.username,
            user.role,
            tenant_id=user.tenant_id,
            ai_route_preference=user.ai_route_preference or "auto",
            scopes=hydrate_scopes_for_role([], user.role),
        )
        set_auth_cookie(response, token_model.access_token)
        return token_model
    finally:
        released = await redis.release_lock(BOOTSTRAP_LOCK_KEY)
        if not released:
            logger.warning("bootstrap_lock_release_failed: key=%s", BOOTSTRAP_LOCK_KEY)
