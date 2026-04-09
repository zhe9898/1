"""
ZEN70 Auth Shared - shared helpers used across auth modules.
"""

from __future__ import annotations

import logging
import sys

import bcrypt
from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.auth.access_policy import is_superadmin_role
from backend.control_plane.auth.auth_helpers import CODE_DB_UNAVAILABLE, CODE_FORBIDDEN, log_auth, zen
from backend.control_plane.auth.jwt import get_access_token_expire_seconds
from backend.models.user import User
from backend.platform.db.rls import set_tenant_context as _set_tenant_context_impl

_logger = logging.getLogger(__name__)


def _auth_mod() -> object:  # noqa: ANN202
    mod = sys.modules.get("backend.api.auth")
    if mod is not None:
        return mod

    class _Fallback:
        set_tenant_context = staticmethod(_set_tenant_context_impl)

    return _Fallback()


BCRYPT_ROUNDS = 12


def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def request_tenant_id(value: str | None) -> str:
    tenant_id = (value or "default").strip()
    return tenant_id or "default"


def assert_user_active(
    user: User,
    *,
    flow: str,
    rid: str,
    username: str | None = None,
    client_ip_str: str | None = None,
) -> None:
    if user.is_active:
        return
    log_auth(flow, False, rid, username=username or user.username, client_ip_str=client_ip_str, detail="inactive_user")
    raise zen(
        CODE_FORBIDDEN,
        "Account is disabled",
        status.HTTP_403_FORBIDDEN,
        recovery_hint="Contact your tenant administrator to reactivate this account",
    )


async def first_user_or_schema_unavailable(db: AsyncSession) -> User | None:
    try:
        result = await db.execute(select(User).limit(1))
        return result.scalar_one_or_none()
    except ProgrammingError as exc:
        msg = str(exc).lower()
        if 'relation "users" does not exist' not in msg and "undefinedtableerror" not in msg:
            raise
        raise zen(
            CODE_DB_UNAVAILABLE,
            "Database schema not initialized",
            status.HTTP_503_SERVICE_UNAVAILABLE,
            recovery_hint="Run bootstrap or migrations before handling auth traffic",
        ) from exc


async def bind_admin_scope(db: AsyncSession, current_admin: dict[str, str]) -> str | None:
    if is_superadmin_role(current_admin):
        return None
    tenant_id = str(current_admin.get("tenant_id") or "default")
    await _auth_mod().set_tenant_context(db, tenant_id)  # type: ignore[attr-defined]
    return tenant_id


def enforce_admin_scope(current_admin: dict[str, str], tenant_id: str, *, action: str) -> None:
    scoped_tenant = None if is_superadmin_role(current_admin) else str(current_admin.get("tenant_id") or "default")
    if scoped_tenant is not None and tenant_id != scoped_tenant:
        raise zen(
            "ZEN-AUTH-403",
            f"Tenant-scoped admin cannot {action} resources outside its tenant",
            status.HTTP_403_FORBIDDEN,
            recovery_hint="Use a superadmin token for global administration or switch to the matching tenant",
            details={"tenant_id": tenant_id, "admin_tenant_id": scoped_tenant, "action": action},
        )


async def register_login_session(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    username: str,
    session_id: str,
    token_id: str,
    ip_address: str | None,
    user_agent: str | None,
    auth_method: str,
    redis: object | None = None,
) -> None:
    from backend.control_plane.auth.sessions import create_session

    await create_session(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        username=username,
        session_id=session_id,
        jti=token_id,
        ip_address=ip_address,
        user_agent=user_agent,
        auth_method=auth_method,
        expires_in_seconds=get_access_token_expire_seconds(),
        redis=redis,
    )
