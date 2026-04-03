"""
ZEN70 Auth Shared - 共享辅助函数（所有 auth 子模块使用）
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from typing import cast

import bcrypt
from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import is_superadmin_role
from backend.api.models.auth import TokenResponse
from backend.core.auth_helpers import (
    CODE_DB_UNAVAILABLE,
    CODE_FORBIDDEN,
    log_auth,
)
from backend.core.auth_helpers import token_response as _token_response_impl
from backend.core.auth_helpers import (
    zen,
)
from backend.core.jwt import get_access_token_expire_seconds
from backend.core.rls import set_tenant_context as _set_tenant_context_impl
from backend.models.user import User

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 可测试间接层：测试通过 patch backend.api.auth.token_response / set_tenant_context
# 以下函数透过 sys.modules 查找 backend.api.auth 命名空间，保证 mock 生效
# ---------------------------------------------------------------------------


def _auth_mod() -> object:  # noqa: ANN202
    mod = sys.modules.get("backend.api.auth")
    if mod is not None:
        return mod

    # 模块尚未加载时回退到实现
    class _Fallback:
        token_response = staticmethod(_token_response_impl)
        set_tenant_context = staticmethod(_set_tenant_context_impl)

    return _Fallback()


BCRYPT_ROUNDS = 12


def build_token_response_model(
    sub: str,
    username: str,
    role: str = "user",
    *,
    tenant_id: str = "default",
    ai_route_preference: str = "auto",
    scopes: list[str] | None = None,
) -> TokenResponse:
    """统一构造 TokenResponse 模型。"""
    body = _auth_mod().token_response(sub, username, role, tenant_id=tenant_id, ai_route_preference=ai_route_preference, scopes=scopes)  # type: ignore[attr-defined]
    return TokenResponse(
        access_token=str(body["access_token"]),
        token_type=str(body["token_type"]),
        expires_in=int(body["expires_in"]),
    )


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
    raise zen(CODE_FORBIDDEN, "Account is disabled", status.HTTP_403_FORBIDDEN, recovery_hint="Contact your tenant administrator to reactivate this account")


async def first_user_or_schema_unavailable(db: AsyncSession) -> User | None:
    """读取首个用户；若 schema 未初始化则返回显式 503。"""
    try:
        result = await db.execute(select(User).limit(1))
        return cast("User | None", result.scalar_one_or_none())
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
    """租户管理员默认绑定自身租户；保留 superadmin 的全局治理口。"""
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


# ── Session/Token Lifecycle Helpers ──────────────────────────────────


def extract_jti_from_token(access_token: str) -> str | None:
    """Extract jti claim from a JWT without verification (payload is base64)."""
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        jti: str | None = payload.get("jti")
        return jti
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None


async def register_login_session(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    username: str,
    access_token: str,
    ip_address: str | None,
    user_agent: str | None,
    auth_method: str,
) -> None:
    """Create a session record after successful login (best-effort).

    Connects the JWT jti to a trackable session so that session
    revocation can also blacklist the corresponding token.
    """
    jti = extract_jti_from_token(access_token)
    if not jti:
        return
    try:
        from backend.core.sessions import create_session

        await create_session(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            username=username,
            jti=jti,
            ip_address=ip_address,
            user_agent=user_agent,
            auth_method=auth_method,
            expires_in_seconds=get_access_token_expire_seconds(),
        )
    except Exception:
        _logger.warning("Session creation failed (best-effort); login proceeds without session tracking", exc_info=True)
