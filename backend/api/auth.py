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

from backend.api import auth_bootstrap
from backend.api import auth_invite
from backend.api import auth_password
from backend.api import auth_pin
from backend.api import auth_user
from backend.api import auth_webauthn
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
