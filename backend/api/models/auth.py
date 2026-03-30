"""
ZEN70 认证相关 Pydantic 模型：WebAuthn 注册/登录、PIN、令牌响应。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 法典 §1.2 RBAC 角色白名单 — 新增角色须同步更新前端 v-if 条件渲染矩阵
ALLOWED_ROLES = Literal["admin", "family", "geek", "child", "elder", "guest"]


class WebAuthnRegisterBeginRequest(BaseModel):
    """WebAuthn 注册开始请求。"""

    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)


class WebAuthnRegisterBeginResponse(BaseModel):
    """WebAuthn 注册开始响应：options 可直接传给 navigator.credentials.create()。"""

    options: dict[str, object] = Field(..., description="JSON 格式的 PublicKeyCredentialCreationOptions")


class WebAuthnRegisterCompleteRequest(BaseModel):
    """WebAuthn 注册完成请求：前端传来的 credential 对象。"""

    credential: dict[str, object] = Field(..., description="navigator.credentials.create() 的返回值")


class WebAuthnLoginBeginRequest(BaseModel):
    """WebAuthn 登录开始请求。"""

    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=64)


class WebAuthnLoginBeginResponse(BaseModel):
    """WebAuthn 登录开始响应。"""

    options: dict[str, object] = Field(..., description="JSON 格式的 PublicKeyCredentialRequestOptions")


class WebAuthnLoginCompleteRequest(BaseModel):
    """WebAuthn 登录完成请求。"""

    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=64)
    credential: dict[str, object] = Field(..., description="navigator.credentials.get() 的返回值")


class TokenResponse(BaseModel):
    """令牌响应。"""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer")
    expires_in: int = Field(..., description="过期秒数")


class PinLoginRequest(BaseModel):
    """PIN 码登录请求（内网降级）。"""

    pin: str = Field(..., min_length=6, max_length=6, description="6 位 PIN")
    tenant_id: str = Field(default="default", min_length=1, max_length=64, description="租户 ID")
    username: str | None = Field(default="family", description="用户名，默认 family")


class PinSetRequest(BaseModel):
    """设置/重置 PIN（需已登录；若有旧 PIN 则必填 pin_old）。"""

    pin_new: str = Field(..., min_length=6, max_length=6, description="新 6 位 PIN")
    pin_old: str | None = Field(
        default=None,
        min_length=6,
        max_length=6,
        description="旧 PIN，若账户已有 PIN 则必填",
    )


class BootstrapRequest(BaseModel):
    """系统接管初始化请求。"""

    username: str = Field(..., min_length=1, max_length=64, description="管理员用户名")
    password: str = Field(..., min_length=8, description="管理员强密码（至少8位）")
    display_name: str = Field(default="ZEN70 Admin", max_length=128, description="显示名称")


class PasswordLoginRequest(BaseModel):
    """账号密码直连登录请求。"""

    tenant_id: str = Field(default="default", min_length=1, max_length=64, description="租户 ID")
    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    password: str = Field(..., min_length=1, description="密码")


class CreateUserRequest(BaseModel):
    """管理员新建账号请求。"""

    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    password: str = Field(..., min_length=8, description="初始密码（与 bootstrap 对齐，至少8位）")
    display_name: str = Field(default="", max_length=128, description="显示名称")
    role: ALLOWED_ROLES = Field(default="family", description="角色(admin/family/geek/child/elder/guest)")
    tenant_id: str = Field(..., min_length=1, max_length=64, description="数据隔离域(多租户ID)")


class UserItem(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str
    tenant_id: str
    is_active: bool
    has_password: bool
    webauthn_credentials: list[dict]


class UserListResponse(BaseModel):
    users: list[UserItem]


class InviteCreateRequest(BaseModel):
    """管理员生成一次性邀请链接请求。"""

    user_id: int = Field(..., description="目标用户ID")
    expires_in_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="有效时长(分钟)，1~1440(24小时)",
    )


class InviteResponse(BaseModel):
    """一次性邀请链接响应。"""

    token: str = Field(..., description="物理防腐的一次性 Token")
    expires_at: int = Field(..., description="过期时间戳")


class AiRoutePreferenceRequest(BaseModel):
    """用户 AI 算力偏好切换。"""

    preference: str = Field(..., description="'local', 'cloud', 'auto'")
