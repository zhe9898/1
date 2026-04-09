"""User management and session projection endpoints."""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.auth_cookies import set_auth_cookie
from backend.api.auth_session_projection import build_authenticated_session_response
from backend.api.auth_shared import bind_admin_scope, enforce_admin_scope
from backend.api.auth_token_issue import issue_auth_token
from backend.api.deps import get_current_admin, get_current_user, get_current_user_optional, get_db, get_tenant_db
from backend.api.models.auth import AiRoutePreferenceRequest, AuthSessionResponse, CreateUserRequest, UserItem, UserListResponse
from backend.control_plane.auth.auth_helpers import CODE_BAD_REQUEST, CODE_NOT_FOUND, log_auth, request_id, zen
from backend.control_plane.auth.permissions import filter_valid_scopes
from backend.control_plane.auth.role_claims import normalize_ai_route_preference, normalize_role_name
from backend.control_plane.auth.sessions import rotate_session_credentials
from backend.control_plane.cache_headers import apply_identity_no_store_headers
from backend.models.user import User, WebAuthnCredential

router = APIRouter()

BCRYPT_ROUNDS = 12


@router.patch("/me/ai-preference", response_model=AuthSessionResponse)
async def update_ai_preference(
    req: AiRoutePreferenceRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: dict[str, str] = Depends(get_current_user),
) -> AuthSessionResponse:
    """Update the caller's AI route preference and re-issue the session token immediately."""
    raw_preference = req.preference.strip().lower()
    normalized_preference = normalize_ai_route_preference(raw_preference)
    if normalized_preference != raw_preference:
        raise zen(CODE_BAD_REQUEST, "Invalid preference value", status.HTTP_400_BAD_REQUEST)

    username = current_user.get("username")
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)

    user.ai_route_preference = normalized_preference
    await db.flush()
    await db.commit()
    log_auth("ai_preference_update", True, request_id(request), username=username, detail=f"changed_to_{normalized_preference}")

    from backend.control_plane.auth.permissions import get_user_scopes, hydrate_scopes_for_role

    user_scopes = hydrate_scopes_for_role(
        await get_user_scopes(db, tenant_id=user.tenant_id, user_id=str(user.id)),
        user.role,
    )
    session_id = str(current_user.get("sid") or "").strip()
    if not session_id:
        raise zen("ZEN-AUTH-401", "Session context is missing", status.HTTP_401_UNAUTHORIZED)

    issued_token = issue_auth_token(
        sub=str(user.id),
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        ai_route_preference=user.ai_route_preference,
        scopes=user_scopes,
        session_id=session_id,
    )
    await rotate_session_credentials(
        db,
        tenant_id=user.tenant_id,
        user_id=str(user.id),
        session_id=issued_token.session_id,
        new_jti=issued_token.token_id,
        expires_in_seconds=issued_token.expires_in,
    )
    set_auth_cookie(response, issued_token.access_token)
    return build_authenticated_session_response(
        sub=str(user.id),
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
        ai_route_preference=user.ai_route_preference,
        scopes=user_scopes,
        expires_in=issued_token.expires_in,
    )


@router.get("/session", response_model=AuthSessionResponse)
async def get_auth_session(
    response: Response,
    current_user: dict[str, object] | None = Depends(get_current_user_optional),
) -> AuthSessionResponse:
    apply_identity_no_store_headers(response)
    if not current_user:
        return AuthSessionResponse(authenticated=False)

    raw_exp = current_user.get("exp")
    raw_scopes = current_user.get("scopes")
    return AuthSessionResponse(
        authenticated=True,
        sub=str(current_user.get("sub") or "") or None,
        username=str(current_user.get("username") or "") or None,
        role=normalize_role_name(current_user.get("role"), fallback="user"),
        tenant_id=str(current_user.get("tenant_id") or "") or None,
        scopes=filter_valid_scopes(raw_scopes if isinstance(raw_scopes, (list, tuple, set)) else None),
        ai_route_preference=normalize_ai_route_preference(current_user.get("ai_route_preference")),
        exp=raw_exp if isinstance(raw_exp, int) and not isinstance(raw_exp, bool) else None,
    )


@router.get("/users", response_model=UserListResponse)
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_admin: dict[str, str] = Depends(get_current_admin),
) -> UserListResponse:
    """List tenant-scoped users together with their registered WebAuthn credentials."""
    scope_tenant_id = await bind_admin_scope(db, current_admin)
    stmt = select(User).options(selectinload(User.credentials))
    if scope_tenant_id is not None:
        stmt = stmt.where(User.tenant_id == scope_tenant_id)
    result = await db.execute(stmt.order_by(User.id.asc()))
    users = result.scalars().all()

    user_items = []
    for user in users:
        credentials = [{"id": cred.credential_id, "name": cred.device_name, "created_at": str(cred.created_at)} for cred in user.credentials]
        user_items.append(
            UserItem(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                role=normalize_role_name(user.role),
                tenant_id=user.tenant_id,
                is_active=user.is_active,
                has_password=bool(user.password_hash),
                webauthn_credentials=credentials,
            )
        )
    return UserListResponse(users=user_items)


@router.post("/users", response_model=UserItem)
async def create_user(
    req: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: dict[str, str] = Depends(get_current_admin),
) -> UserItem:
    """Create a tenant-scoped user from the control plane."""
    await bind_admin_scope(db, current_admin)
    enforce_admin_scope(current_admin, req.tenant_id, action="create users")
    result = await db.execute(select(User).where(User.tenant_id == req.tenant_id, User.username == req.username))
    if result.scalar_one_or_none():
        raise zen(CODE_BAD_REQUEST, "Username already exists", status.HTTP_400_BAD_REQUEST)

    hashed_pw = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")
    user = User(
        username=req.username,
        display_name=req.display_name,
        role=normalize_role_name(req.role),
        password_hash=hashed_pw,
        tenant_id=req.tenant_id,
    )
    db.add(user)
    await db.flush()

    return UserItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=normalize_role_name(user.role),
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
    """Revoke a single WebAuthn credential."""
    scope_tenant_id = await bind_admin_scope(db, current_admin)
    stmt = select(WebAuthnCredential, User).join(User, User.id == WebAuthnCredential.user_id).where(WebAuthnCredential.credential_id == credential_id)
    if scope_tenant_id is not None:
        stmt = stmt.where(User.tenant_id == scope_tenant_id)
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        raise zen(CODE_NOT_FOUND, "Credential not found", status.HTTP_404_NOT_FOUND)
    credential, _user = row
    await db.delete(credential)
    await db.flush()
    return {"status": "ok", "message": "Credential revoked successfully"}
