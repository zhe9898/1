"""Authentication-related Pydantic models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ALLOWED_ROLES = Literal["admin", "family", "geek", "child", "elder", "guest"]
PIN_LENGTH = 8


class WebAuthnRegisterBeginRequest(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)


class WebAuthnRegisterBeginResponse(BaseModel):
    options: dict[str, object] = Field(..., description="JSON PublicKeyCredentialCreationOptions")


class WebAuthnRegisterCompleteRequest(BaseModel):
    credential: dict[str, object] = Field(..., description="Credential object from navigator.credentials.create()")


class WebAuthnLoginBeginRequest(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=64)


class WebAuthnLoginBeginResponse(BaseModel):
    options: dict[str, object] = Field(..., description="JSON PublicKeyCredentialRequestOptions")


class WebAuthnLoginCompleteRequest(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=64)
    credential: dict[str, object] = Field(..., description="Credential object from navigator.credentials.get()")


class AuthSessionResponse(BaseModel):
    authenticated: bool = False
    sub: str | None = None
    username: str | None = None
    role: str | None = None
    tenant_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    ai_route_preference: str = "auto"
    exp: int | None = None


class PinLoginRequest(BaseModel):
    pin: str = Field(..., min_length=PIN_LENGTH, max_length=PIN_LENGTH, pattern=r"^\d+$", description="8-digit PIN")
    tenant_id: str = Field(default="default", min_length=1, max_length=64, description="Tenant ID")
    username: str | None = Field(default="family", description="Username, default family")


class PinSetRequest(BaseModel):
    pin_new: str = Field(..., min_length=PIN_LENGTH, max_length=PIN_LENGTH, pattern=r"^\d+$", description="New 8-digit PIN")
    pin_old: str | None = Field(
        default=None,
        min_length=PIN_LENGTH,
        max_length=PIN_LENGTH,
        pattern=r"^\d+$",
        description="Old 8-digit PIN, required when changing existing PIN",
    )


class BootstrapRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, description="Admin username")
    password: str = Field(..., min_length=8, description="Admin password, at least 8 chars")
    display_name: str = Field(default="ZEN70 Admin", max_length=128, description="Display name")


class PasswordLoginRequest(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64, description="Tenant ID")
    username: str = Field(..., min_length=1, max_length=64, description="Username")
    password: str = Field(..., min_length=1, description="Password")


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, description="Username")
    password: str = Field(..., min_length=8, description="Initial password")
    display_name: str = Field(default="", max_length=128, description="Display name")
    role: ALLOWED_ROLES = Field(default="family", description="Role")
    tenant_id: str = Field(..., min_length=1, max_length=64, description="Tenant ID")


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
    user_id: int = Field(..., description="Target user ID")
    expires_in_minutes: int = Field(default=15, ge=1, le=1440, description="Expiration in minutes")


class InviteResponse(BaseModel):
    token: str = Field(..., description="One-time invite token")
    expires_at: int = Field(..., description="Unix expiration timestamp")


class AiRoutePreferenceRequest(BaseModel):
    preference: str = Field(..., description="'local', 'cloud', or 'auto'")
