"""User management and session projection endpoints."""

from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.control_plane.adapters.auth_cookies import clear_auth_cookie, set_auth_cookie
from backend.control_plane.adapters.auth_session_projection import build_authenticated_session_response
from backend.control_plane.adapters.auth_shared import (
    bind_admin_scope,
    build_auth_actor_payload,
    enforce_admin_scope,
    require_auth_username,
    resolve_auth_actor,
    should_clear_auth_cookie_for_self_target,
)
from backend.control_plane.adapters.auth_token_issue import issue_auth_token
from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.deps import get_current_admin, get_current_user, get_current_user_optional, get_db, get_tenant_db
from backend.control_plane.adapters.models.auth import AiRoutePreferenceRequest, AuthSessionResponse, CreateUserRequest, UserItem, UserListResponse
from backend.control_plane.auth.auth_helpers import CODE_BAD_REQUEST, CODE_NOT_FOUND, log_auth, request_id, zen
from backend.control_plane.auth.permissions import filter_valid_scopes
from backend.control_plane.auth.sessions import revoke_all_user_sessions, rotate_session_credentials
from backend.control_plane.cache_headers import apply_identity_no_store_headers
from backend.kernel.contracts.role_claims import current_user_role, normalize_ai_route_preference, normalize_role_name
from backend.kernel.contracts.tenant_claims import current_user_tenant_id, require_current_user_tenant_id
from backend.models.user import User, WebAuthnCredential
from backend.platform.logging.audit import log_audit
from backend.platform.redis.client import CHANNEL_USER_EVENTS

router = APIRouter()

BCRYPT_ROUNDS = 12
_CURRENT_ADMIN_DEP = Depends(get_current_admin)
_CURRENT_USER_DEP = Depends(get_current_user)
_CURRENT_USER_OPTIONAL_DEP = Depends(get_current_user_optional)
_DB_DEP = Depends(get_db)
_TENANT_DB_DEP = Depends(get_tenant_db)


def _clear_auth_cookie_for_self_credential_revocation(
    response: Response,
    *,
    current_user: dict[str, object],
    target_user_id: str,
) -> None:
    if should_clear_auth_cookie_for_self_target(current_user, target_user_id=target_user_id):
        clear_auth_cookie(response)


async def _record_webauthn_credential_revocation_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    current_user: dict[str, object],
    user: User,
    credential: WebAuthnCredential,
    revoked_sessions: int,
) -> None:
    actor = resolve_auth_actor(current_user)
    await log_audit(
        db,
        tenant_id=tenant_id,
        action="auth.webauthn.credential.revoked",
        result="success",
        user_id=actor.user_id,
        username=actor.username,
        resource_type="webauthn_credential",
        resource_id=credential.credential_id,
        details={
            "target_user_id": str(user.id),
            "target_username": user.username,
            "credential_id": credential.credential_id,
            "device_name": credential.device_name,
            "revoked_sessions": revoked_sessions,
        },
    )


async def _publish_webauthn_credential_revocation_event(
    *,
    tenant_id: str,
    current_user: dict[str, object],
    user: User,
    credential: WebAuthnCredential,
    revoked_sessions: int,
) -> None:
    await publish_control_event(
        CHANNEL_USER_EVENTS,
        "webauthn_credential_revoked",
        {
            "target_user_id": str(user.id),
            "user": {
                "id": str(user.id),
                "username": user.username,
            },
            "credential": {
                "id": credential.credential_id,
                "device_name": credential.device_name,
            },
            "revoked_sessions": revoked_sessions,
            "actor": build_auth_actor_payload(current_user),
        },
        tenant_id=tenant_id,
    )


async def _record_user_provisioning_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    current_user: dict[str, object],
    user: User,
) -> None:
    actor = resolve_auth_actor(current_user)
    await log_audit(
        db,
        tenant_id=tenant_id,
        action="user.created",
        result="success",
        user_id=actor.user_id,
        username=actor.username,
        resource_type="user",
        resource_id=str(user.id),
        details={
            "target_user_id": str(user.id),
            "target_username": user.username,
            "target_role": normalize_role_name(user.role),
            "has_password": bool(user.password_hash),
        },
    )


async def _publish_user_provisioning_event(
    *,
    tenant_id: str,
    current_user: dict[str, object],
    user: UserItem,
) -> None:
    await publish_control_event(
        CHANNEL_USER_EVENTS,
        "user_created",
        {
            "user": user.model_dump(mode="json"),
            "actor": build_auth_actor_payload(current_user),
        },
        tenant_id=tenant_id,
    )


@router.patch("/me/ai-preference", response_model=AuthSessionResponse)
async def update_ai_preference(
    req: AiRoutePreferenceRequest,
    request: Request,
    response: Response,
    db: AsyncSession = _TENANT_DB_DEP,
    current_user: dict[str, object] = _CURRENT_USER_DEP,
) -> AuthSessionResponse:
    """Update the caller's AI route preference and re-issue the session token immediately."""
    raw_preference = req.preference.strip().lower()
    normalized_preference = normalize_ai_route_preference(raw_preference)
    if normalized_preference != raw_preference:
        raise zen(CODE_BAD_REQUEST, "Invalid preference value", status.HTTP_400_BAD_REQUEST)

    username = require_auth_username(current_user)
    tenant_id = require_current_user_tenant_id(current_user)
    result = await db.execute(select(User).where(User.tenant_id == tenant_id, User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise zen(CODE_NOT_FOUND, "User not found", status.HTTP_404_NOT_FOUND)

    user.ai_route_preference = normalized_preference
    await db.flush()
    await db.commit()
    log_auth("ai_preference_update", True, request_id(request), username=user.username, detail=f"changed_to_{normalized_preference}")

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
    current_user: dict[str, object] | None = _CURRENT_USER_OPTIONAL_DEP,
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
        role=current_user_role(current_user),
        tenant_id=current_user_tenant_id(current_user),
        scopes=filter_valid_scopes(raw_scopes if isinstance(raw_scopes, (list, tuple, set)) else None),
        ai_route_preference=normalize_ai_route_preference(current_user.get("ai_route_preference")),
        exp=raw_exp if isinstance(raw_exp, int) and not isinstance(raw_exp, bool) else None,
    )


@router.get("/users", response_model=UserListResponse)
async def list_users(
    db: AsyncSession = _DB_DEP,
    current_admin: dict[str, object] = _CURRENT_ADMIN_DEP,
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
    db: AsyncSession = _DB_DEP,
    current_admin: dict[str, object] = _CURRENT_ADMIN_DEP,
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
    user_item = UserItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=normalize_role_name(user.role),
        tenant_id=user.tenant_id,
        is_active=user.is_active,
        has_password=True,
        webauthn_credentials=[],
    )
    await _record_user_provisioning_audit(
        db,
        tenant_id=user.tenant_id,
        current_user=current_admin,
        user=user,
    )
    await db.commit()
    await _publish_user_provisioning_event(
        tenant_id=user.tenant_id,
        current_user=current_admin,
        user=user_item,
    )
    return user_item


@router.delete("/credentials/{credential_id}")
async def revoke_credential(
    credential_id: str,
    response: Response,
    db: AsyncSession = _DB_DEP,
    current_admin: dict[str, object] = _CURRENT_ADMIN_DEP,
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
    credential, user = row
    tenant_id = str(user.tenant_id).strip()
    actor_username = str(current_admin.get("username") or "").strip() or "unknown"
    await db.delete(credential)
    await db.flush()
    revoked_sessions = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=str(user.id),
        revoked_by=f"admin:credential_revoke:{actor_username}",
        redis=None,
    )
    await _record_webauthn_credential_revocation_audit(
        db,
        tenant_id=tenant_id,
        current_user=current_admin,
        user=user,
        credential=credential,
        revoked_sessions=revoked_sessions,
    )
    await db.commit()
    _clear_auth_cookie_for_self_credential_revocation(
        response,
        current_user=current_admin,
        target_user_id=str(user.id),
    )
    await _publish_webauthn_credential_revocation_event(
        tenant_id=tenant_id,
        current_user=current_admin,
        user=user,
        credential=credential,
        revoked_sessions=revoked_sessions,
    )
    return {"status": "ok", "message": "Credential revoked successfully"}
