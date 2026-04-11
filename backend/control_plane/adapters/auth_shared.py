"""
ZEN70 Auth Shared - shared helpers used across auth modules.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

import bcrypt
from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.auth.access_policy import is_superadmin_role
from backend.control_plane.auth.auth_helpers import CODE_DB_UNAVAILABLE, CODE_FORBIDDEN, log_auth, zen
from backend.control_plane.auth.jwt import get_access_token_expire_seconds
from backend.kernel.contracts.tenant_claims import normalize_tenant_claim, require_current_user_tenant_id
from backend.models.user import User
from backend.platform.db.rls import set_tenant_context

_logger = logging.getLogger(__name__)


BCRYPT_ROUNDS = 12


@dataclass(frozen=True, slots=True)
class AuthActor:
    user_id: str | None
    username: str | None
    session_id: str | None


def _normalize_claim(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized_value = value.strip()
    return normalized_value or None


def resolve_auth_actor(current_user: Mapping[str, object]) -> AuthActor:
    return AuthActor(
        user_id=_normalize_claim(current_user.get("sub")),
        username=_normalize_claim(current_user.get("username")),
        session_id=_normalize_claim(current_user.get("sid")),
    )


def build_auth_actor_payload(current_user: Mapping[str, object]) -> dict[str, str | None]:
    actor = resolve_auth_actor(current_user)
    return {
        "user_id": actor.user_id,
        "username": actor.username,
    }


def should_clear_auth_cookie_for_self_target(
    current_user: Mapping[str, object],
    *,
    target_user_id: str,
    target_session_id: str | None = None,
) -> bool:
    actor = resolve_auth_actor(current_user)
    normalized_target_user_id = _normalize_claim(target_user_id)
    if actor.user_id is None or normalized_target_user_id is None or actor.user_id != normalized_target_user_id:
        return False
    if target_session_id is None:
        return True
    normalized_target_session_id = _normalize_claim(target_session_id)
    return actor.session_id is not None and normalized_target_session_id is not None and actor.session_id == normalized_target_session_id


def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def request_tenant_id(value: str | None) -> str:
    tenant_id = normalize_tenant_claim(value)
    if tenant_id is not None:
        return tenant_id
    raise zen(
        "ZEN-TENANT-4001",
        "tenant_id is required for tenant-scoped authentication flows",
        status.HTTP_400_BAD_REQUEST,
        recovery_hint="Pass the target tenant_id explicitly when starting password, PIN, or WebAuthn authentication",
    )


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
    tenant_id = require_current_user_tenant_id(current_admin)
    await set_tenant_context(db, tenant_id)
    return tenant_id


def enforce_admin_scope(current_admin: dict[str, str], tenant_id: str, *, action: str) -> None:
    scoped_tenant = None if is_superadmin_role(current_admin) else require_current_user_tenant_id(current_admin)
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
