from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from functools import lru_cache
from typing import Any, Callable

from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth_cookies import get_auth_cookie_token, set_auth_cookie
from backend.core.errors import zen
from backend.core.jwt import decode_token
from backend.core.node_auth import authenticate_node_request
from backend.core.redis_client import RedisClient
from backend.core.rls import assert_rls_ready, set_tenant_context
from backend.db import get_db_session
from backend.models.user import User

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)
_node_bearer = HTTPBearer(auto_error=False)
ADMIN_ROLES = frozenset({"admin", "superadmin"})
SUPERADMIN_ROLE = "superadmin"
_MACHINE_TENANT_BODY_TIMEOUT_SECONDS = float(os.getenv("MACHINE_TENANT_BODY_TIMEOUT_SECONDS", "2.0"))


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


async def get_db_optional() -> AsyncIterator[AsyncSession | None]:
    if not os.getenv("POSTGRES_DSN"):
        yield None
        return
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
    db: AsyncSession | None = Depends(get_db_optional),
) -> dict[str, object]:
    del credentials
    access_token = get_auth_cookie_token(request) or ""
    if not access_token:
        raise zen("ZEN-AUTH-401", "Missing or invalid token", status_code=401)

    redis_client = get_redis(request)
    redis_conn = getattr(redis_client, "redis", None) if redis_client else None
    payload, new_token = await decode_token(access_token, redis_conn=redis_conn)
    if db is None:
        raise zen("ZEN-BUS-5030", "Database unavailable for token subject validation", status_code=503)
    await _assert_token_subject_active(db, payload)
    if new_token:
        set_auth_cookie(response, new_token)
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
        raise zen(
            "ZEN-TENANT-4002",
            "Tenant context is missing from authentication token",
            status_code=403,
            recovery_hint="Re-authenticate to obtain a token that includes a valid tenant_id claim",
        )
    return await _bind_tenant_db(db, tenant_id)


async def _read_machine_request_payload(request: Request) -> dict[str, object]:
    try:
        payload = await asyncio.wait_for(request.json(), timeout=_MACHINE_TENANT_BODY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise zen(
            "ZEN-TENANT-4080",
            "Timed out while reading machine request body",
            status_code=408,
            recovery_hint="Retry the machine request and complete the request body promptly",
        ) from exc
    except (json.JSONDecodeError, RuntimeError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_machine_node_token_from_request(request: Request) -> str:
    try:
        authorization = request.headers.get("Authorization")
    except Exception:
        authorization = None
    if not isinstance(authorization, str):
        raise zen(
            "ZEN-NODE-4011",
            "Missing node token",
            status_code=401,
            recovery_hint="Attach Authorization: Bearer <node_token> to node control-plane requests",
        )

    scheme, _, credentials = authorization.partition(" ")
    if scheme.strip().lower() != "bearer" or not credentials.strip():
        raise zen(
            "ZEN-NODE-4011",
            "Missing node token",
            status_code=401,
            recovery_hint="Attach Authorization: Bearer <node_token> to node control-plane requests",
        )
    return credentials.strip()


async def get_machine_tenant_db(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    node_token = _extract_machine_node_token_from_request(request)
    payload = await _read_machine_request_payload(request)

    node_id = payload.get("node_id")
    normalized_node_id = node_id.strip() if isinstance(node_id, str) else ""
    if not normalized_node_id:
        raise zen(
            "ZEN-NODE-4001",
            "Machine request is missing node_id",
            status_code=400,
            recovery_hint="Include node_id in the request body for machine-authenticated control-plane calls",
        )

    node = await authenticate_node_request(
        db,
        normalized_node_id,
        node_token,
        require_active=False,
    )
    authoritative_tenant_id = str(node.tenant_id).strip()
    body_tenant_id = payload.get("tenant_id")
    normalized_body_tenant_id = body_tenant_id.strip() if isinstance(body_tenant_id, str) else ""
    if normalized_body_tenant_id and normalized_body_tenant_id != authoritative_tenant_id:
        logger.warning(
            "machine tenant mismatch ignored for node %s: body tenant=%s authoritative tenant=%s",
            normalized_node_id,
            normalized_body_tenant_id,
            authoritative_tenant_id,
        )

    request.state.machine_tenant_id = authoritative_tenant_id
    request.state.machine_node_id = normalized_node_id
    return await _bind_tenant_db(db, authoritative_tenant_id)


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
    del credentials
    access_token = get_auth_cookie_token(request) or ""
    if not access_token:
        return None
    try:
        redis_client = get_redis(request)
        redis_conn = getattr(redis_client, "redis", None) if redis_client else None
        payload, new_token = await decode_token(access_token, redis_conn=redis_conn)
        if new_token:
            set_auth_cookie(response, new_token)
        return payload
    except Exception as exc:
        logger.debug("optional auth failed (%s): %s", type(exc).__name__, exc)
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


def require_scope(required_scope: str) -> Callable[..., Any]:
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
        normalized_scopes = {str(scope).strip().lower() for scope in scopes if isinstance(scope, str) and scope.strip()}
        required_scope_normalized = required_scope.strip().lower()

        # Check if user has the required scope
        if required_scope_normalized not in normalized_scopes:
            raise zen(
                "ZEN-AUTH-403",
                f"Missing required permission: {required_scope}",
                status_code=403,
                recovery_hint=f"Request {required_scope} permission from an administrator",
                details={"required_scope": required_scope, "user_scopes": sorted(normalized_scopes)},
            )

        return current_user

    return _check_scope


async def _assert_token_subject_active(db: AsyncSession, payload: Mapping[str, object]) -> None:
    subject = str(payload.get("sub") or "").strip()
    tenant_id = str(payload.get("tenant_id") or "default").strip() or "default"
    if not subject:
        raise zen("ZEN-AUTH-401", "Invalid token subject", status_code=401)

    await set_tenant_context(db, tenant_id)
    query = select(User).where(User.tenant_id == tenant_id)
    if subject.isdigit():
        query = query.where(User.id == int(subject))
    else:
        query = query.where(User.username == subject)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user is None:
        raise zen("ZEN-AUTH-401", "Token subject no longer exists", status_code=401)

    is_active = bool(getattr(user, "is_active", False))
    raw_status = getattr(user, "status", None)
    status_value = raw_status.lower() if isinstance(raw_status, str) and raw_status else "active"
    if not is_active or status_value != "active":
        raise zen("ZEN-AUTH-401", "Account is disabled", status_code=401, recovery_hint="Re-authenticate after account reactivation")
