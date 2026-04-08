"""
ZEN70 Auth Router - 统一身份认证路由组装

将各子模块路由组合到 /api/v1/auth 前缀下：
  - auth_bootstrap: 系统初始化
  - auth_password:  密码认证
  - auth_webauthn:  WebAuthn 注册/登录
  - auth_pin:       PIN 降级认证
  - auth_user:      账号管理
  - auth_invite:    OOB 邀请系统
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.api import auth_bootstrap, auth_invite, auth_password, auth_pin, auth_user, auth_webauthn
from backend.api import push as push_router

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# 推送通知
router.include_router(push_router.router, prefix="/push", tags=["push"])

# 认证子模块
router.include_router(auth_bootstrap.router)
router.include_router(auth_password.router)
router.include_router(auth_webauthn.router)
router.include_router(auth_pin.router)
router.include_router(auth_user.router)
router.include_router(auth_invite.router)

# Re-export endpoint functions for tests that import from backend.api.auth directly
password_login = auth_password.password_login
login_begin = auth_webauthn.login_begin
login_complete = auth_webauthn.login_complete
pin_login = auth_pin.pin_login
list_users = auth_user.list_users
create_user = auth_user.create_user
revoke_credential = auth_user.revoke_credential
create_invite = auth_invite.create_invite
invite_fallback_login = auth_invite.invite_fallback_login

# Re-export constants for test assertions
PIN_RATE_LIMIT_WINDOW = auth_pin.PIN_RATE_LIMIT_WINDOW
PIN_RATE_LIMIT_MAX = auth_pin.PIN_RATE_LIMIT_MAX

# Re-export helpers so tests can patch on backend.api.auth namespace
from backend.control_plane.auth.auth_helpers import (  # noqa: E402, F401
    check_webauthn_rate_limit,
    consume_challenge,
    credential_id_to_base64url,
    expected_challenge_bytes,
    origin_from_request,
    token_response,
)
from backend.platform.db.rls import set_tenant_context  # noqa: E402, F401

try:
    from backend.control_plane.auth.webauthn import (  # noqa: E402
        generate_authentication_challenge,
        verify_authentication,
    )
except (ImportError, RuntimeError):
    generate_authentication_challenge = None  # type: ignore[assignment]
    verify_authentication = None  # type: ignore[assignment]
