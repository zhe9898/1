"""
ZEN70 Auth User - 账号管理（列表、创建、AI 偏好、吊销凭证）
"""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.auth_shared import bind_admin_scope, build_token_response_model, enforce_admin_scope
from backend.api.deps import get_current_admin, get_current_user, get_db
from backend.api.models.auth import (
    AiRoutePreferenceRequest,
    CreateUserRequest,
    TokenResponse,
    UserItem,
    UserListResponse,
)
from backend.core.auth_helpers import (
    CODE_BAD_REQUEST,
    CODE_NOT_FOUND,
    log_auth,
    request_id,
    zen,
)
from backend.models.user import User, WebAuthnCredential

router = APIRouter()

BCRYPT_ROUNDS = 12


@router.patch("/me/ai-preference", response_model=TokenResponse)
async def update_ai_preference(
    req: AiRoutePreferenceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, str] = Depends(get_current_user),
) -> TokenResponse:
    """法典 M9.4: 调整用户的 AI 计算偏好，并在此刻立刻颁发新 JWT 使配置 0 延迟生效。"""
    if req.preference not in ("local", "cloud", "auto"):
        raise zen(CODE_BAD_REQUEST, "Invalid preference value", status.HTTP_400_BAD_REQUEST)

    username = current_user.get("username")
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)

    user.ai_route_preference = req.preference
    await db.flush()
    log_auth("ai_preference_update", True, request_id(request), username=username, detail=f"changed_to_{req.preference}")
    from backend.core.permissions import get_user_scopes, hydrate_scopes_for_role

    user_scopes = hydrate_scopes_for_role(
        await get_user_scopes(db, tenant_id=user.tenant_id, user_id=str(user.id)),
        user.role,
    )

    return build_token_response_model(
        sub=str(user.id),
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        ai_route_preference=user.ai_route_preference,
        scopes=user_scopes,
    )


@router.get("/users", response_model=UserListResponse)
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_admin: dict[str, str] = Depends(get_current_admin),
) -> UserListResponse:
    """列出所有系统用户及其 WebAuthn 设备"""
    scope_tenant_id = await bind_admin_scope(db, current_admin)
    stmt = select(User).options(selectinload(User.credentials))
    if scope_tenant_id is not None:
        stmt = stmt.where(User.tenant_id == scope_tenant_id)
    result = await db.execute(stmt.order_by(User.id.asc()))
    users = result.scalars().all()

    user_items = []
    for u in users:
        creds = [{"id": c.credential_id, "name": c.device_name, "created_at": str(c.created_at)} for c in u.credentials]
        user_items.append(
            UserItem(
                id=u.id,
                username=u.username,
                display_name=u.display_name,
                role=u.role,
                tenant_id=u.tenant_id,
                is_active=u.is_active,
                has_password=bool(u.password_hash),
                webauthn_credentials=creds,
            )
        )
    return UserListResponse(users=user_items)


@router.post("/users", response_model=UserItem)
async def create_user(
    req: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: dict[str, str] = Depends(get_current_admin),
) -> UserItem:
    """管理员强制后台创建账号"""
    await bind_admin_scope(db, current_admin)
    enforce_admin_scope(current_admin, req.tenant_id, action="create users")
    result = await db.execute(select(User).where(User.tenant_id == req.tenant_id, User.username == req.username))
    if result.scalar_one_or_none():
        raise zen(CODE_BAD_REQUEST, "Username already exists", status.HTTP_400_BAD_REQUEST)

    hashed_pw = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")
    user = User(username=req.username, display_name=req.display_name, role=req.role, password_hash=hashed_pw, tenant_id=req.tenant_id)
    db.add(user)
    await db.flush()

    return UserItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        tenant_id=user.tenant_id,
        is_active=user.is_active,
        has_password=True,
        webauthn_credentials=[],
    )


@router.delete("/credentials/{credential_id}")
async def revoke_credential(
    credential_id: str,
    db: AsyncSession = Depends(get_db),
    current_admin: dict[str, str] = Depends(get_current_admin),
) -> dict[str, str]:
    """吊销（删除）某个指纹/面容设备凭证防丢"""
    scope_tenant_id = await bind_admin_scope(db, current_admin)
    stmt = select(WebAuthnCredential, User).join(User, User.id == WebAuthnCredential.user_id).where(WebAuthnCredential.credential_id == credential_id)
    if scope_tenant_id is not None:
        stmt = stmt.where(User.tenant_id == scope_tenant_id)
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        raise zen(CODE_NOT_FOUND, "Credential not found", status.HTTP_404_NOT_FOUND)
    cred, _user = row
    await db.delete(cred)
    await db.flush()
    return {"status": "ok", "message": "Credential revoked successfully"}
