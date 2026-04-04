from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import zen
from backend.core.jwt import decode_token, is_jti_blacklisted
from backend.core.redis_client import RedisClient
from backend.core.rls import assert_rls_ready, set_tenant_context
from backend.db import get_db_session

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)
_node_bearer = HTTPBearer(auto_error=False)
ADMIN_ROLES = frozenset({"admin", "superadmin"})
SUPERADMIN_ROLE = "superadmin"


@lru_cache
def get_settings() -> dict[str, object]:
    cors = os.getenv("CORS_ORIGINS", "").strip()
    return {
        "redis_host": os.getenv("REDIS_HOST", ""),
        "redis_port": int(os.getenv("REDIS_PORT", "6379")),
        "redis_password": os.getenv("REDIS_PASSWORD") or None,
        "redis_db": int(os.getenv("REDIS_DB", "0")),
        "cors_origins": [origin.strip() for origin in cors.split(",") if origin.strip()] if cors else [],
        "postgres_dsn": os.getenv("POSTGRES_DSN") or None,
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
    }


def get_redis(request: Request) -> RedisClient | None:
    return getattr(request.app.state, "redis", None)


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_db_session():
        yield session


async def _bind_tenant_db(db: AsyncSession, tenant_id: str) -> AsyncSession:
    normalized_tenant_id = (tenant_id or "").strip()
    if not normalized_tenant_id:
        # Reject requests with a missing tenant context rather than silently
        # falling back to "default", which could expose cross-tenant data.
        raise zen(
            "ZEN-TENANT-4002",
            "Tenant context is missing from authentication token",
            status_code=403,
            recovery_hint="Re-authenticate to obtain a token that includes a valid tenant_id claim",
        )
    await set_tenant_context(db, normalized_tenant_id)
    try:
        await assert_rls_ready(db)
    except RuntimeError as exc:
        raise zen(
            "ZEN-BUS-5031",
            "Tenant isolation is not ready",
            status_code=503,
            recovery_hint="Initialize database schema and RLS policies before serving tenant traffic",
            details={"tenant_id": normalized_tenant_id},
        ) from exc
    return db


async def get_current_user(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, object]:
    if not credentials or not credentials.credentials:
        raise zen("ZEN-AUTH-401", "Missing or invalid token", status_code=401)

    redis_client = get_redis(request)
    redis_conn = getattr(redis_client, "redis", None) if redis_client else None
    payload, new_token = await decode_token(credentials.credentials, redis_conn=redis_conn)
    jti = payload.get("jti")
    if jti and await is_jti_blacklisted(redis_conn, jti):
        raise zen("ZEN-AUTH-401", "Token has been revoked", status_code=401)
    if new_token:
        response.headers["X-New-Token"] = new_token
    return payload


async def get_tenant_db(
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    tenant_id = str(current_user.get("tenant_id") or "").strip()
    if not tenant_id:
        logger.warning(
            "JWT tenant_id is missing or empty (sub=%s, role=%s); request rejected to prevent cross-tenant data leak",
            current_user.get("sub"),
            current_user.get("role"),
        )
    return await _bind_tenant_db(db, tenant_id)


def _extract_machine_tenant_id(request: Request) -> str | None:
    from_state = getattr(request.state, "machine_tenant_id", None)
    if isinstance(from_state, str) and from_state.strip():
        return from_state.strip()

    from_header: object = None
    try:
        from_header = request.headers.get("X-Tenant-ID")
    except Exception:
        from_header = None
    if isinstance(from_header, str) and from_header.strip():
        return from_header.strip()

    return None


async def get_machine_tenant_db(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    tenant_id = _extract_machine_tenant_id(request)
    if not tenant_id:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, RuntimeError, ValueError):
            payload = {}
        body_tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
        tenant_id = body_tenant_id.strip() if isinstance(body_tenant_id, str) else ""

    if not tenant_id:
        raise zen(
            "ZEN-TENANT-4001",
            "Machine request is missing tenant_id",
            status_code=400,
            recovery_hint="Attach tenant_id in the machine request payload before authenticating node traffic",
        )
    request.state.machine_tenant_id = tenant_id
    return await _bind_tenant_db(db, tenant_id)


def has_admin_role(current_user: Mapping[str, object] | None) -> bool:
    role = str((current_user or {}).get("role") or "").strip().lower()
    return role in ADMIN_ROLES


def is_superadmin_role(current_user: Mapping[str, object] | None) -> bool:
    role = str((current_user or {}).get("role") or "").strip().lower()
    return role == SUPERADMIN_ROLE


def require_admin_role(current_user: dict[str, object]) -> dict[str, object]:
    if not has_admin_role(current_user):
        raise zen(
            "ZEN-AUTH-403",
            "Admin privileges required",
            status_code=403,
            recovery_hint="Sign in with an admin or superadmin account and retry",
        )
    return current_user


def require_superadmin_role(current_user: dict[str, object]) -> dict[str, object]:
    if not is_superadmin_role(current_user):
        raise zen(
            "ZEN-AUTH-403",
            "Superadmin privileges required",
            status_code=403,
            recovery_hint="Sign in with a superadmin account and retry",
        )
    return current_user


async def get_current_admin(
    current_user: dict[str, object] = Depends(get_current_user),
) -> dict[str, object]:
    return require_admin_role(current_user)


async def get_current_user_optional(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict | None:
    if not credentials or not credentials.credentials:
        return None
    try:
        redis_client = get_redis(request)
        redis_conn = getattr(redis_client, "redis", None) if redis_client else None
        payload, new_token = await decode_token(credentials.credentials, redis_conn=redis_conn)
        jti = payload.get("jti")
        if jti and await is_jti_blacklisted(redis_conn, jti):
            logger.debug("optional auth: token jti=%s is blacklisted, treating as unauthenticated", jti)
            return None
        if new_token:
            response.headers["X-New-Token"] = new_token
        return payload
    except HTTPException as exc:
        logger.debug("optional auth failed (invalid or expired token): %s", type(exc).__name__)
        return None


async def get_node_machine_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_node_bearer),
) -> str:
    if not credentials or not credentials.credentials:
        raise zen(
            "ZEN-NODE-4011",
            "Missing node token",
            status_code=401,
            recovery_hint="Attach Authorization: Bearer <node_token> to node control-plane requests",
        )
    return credentials.credentials


def require_scope(required_scope: str) -> object:
    """Dependency factory for requiring specific permission scope.

    Usage:
        @router.post("/jobs")
        async def create_job(
            current_user: dict = Depends(require_scope("write:jobs")),
        ):
            ...

    Args:
        required_scope: Required scope (e.g., "write:jobs", "admin:nodes")

    Returns:
        Dependency function that checks for the scope
    """

    async def _check_scope(
        current_user: dict[str, object] = Depends(get_current_user),
    ) -> dict[str, object]:
        scopes: object = current_user.get("scopes", [])
        if not isinstance(scopes, list):
            scopes = []

        # Check if user has the required scope
        if required_scope not in scopes:
            # Check if user has admin role (bypass scope check)
            role = current_user.get("role", "")
            if role not in ("admin", "superadmin"):
                raise zen(
                    "ZEN-AUTH-403",
                    f"Missing required permission: {required_scope}",
                    status_code=403,
                    recovery_hint=f"Request {required_scope} permission from an administrator",
                    details={"required_scope": required_scope, "user_scopes": scopes},
                )

        return current_user

    return _check_scope
