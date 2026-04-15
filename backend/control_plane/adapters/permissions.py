"""Permission management API endpoints."""

from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.auth_cookies import clear_auth_cookie
from backend.control_plane.adapters.auth_shared import (
    build_auth_actor_payload,
    require_auth_username,
    resolve_auth_actor,
    should_clear_auth_cookie_for_self_target,
)
from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.deps import get_current_admin, get_tenant_db
from backend.control_plane.auth.permissions import ALLOWED_SCOPES, grant_permission, is_valid_scope, list_user_permissions, normalize_scope, revoke_permission
from backend.control_plane.auth.sessions import revoke_all_user_sessions
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.permission import Permission
from backend.platform.logging.audit import log_audit
from backend.platform.redis.client import CHANNEL_USER_EVENTS

router = APIRouter(prefix="/api/v1/permissions", tags=["permissions"])


class PermissionGrantRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128, pattern=r"^\d+$")
    scope: str = Field(..., min_length=1, max_length=128)
    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=128)
    expires_at: str | None = Field(default=None)

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        normalized = normalize_scope(value)
        if not is_valid_scope(normalized):
            raise ValueError(f"Invalid scope. Allowed scopes: {', '.join(sorted(ALLOWED_SCOPES))}")
        return normalized


class PermissionResponse(BaseModel):
    id: int
    tenant_id: str
    user_id: str
    scope: str
    resource_type: str | None
    resource_id: str | None
    granted_by: str
    granted_at: str
    expires_at: str | None


def _to_response(permission: Permission) -> PermissionResponse:
    return PermissionResponse(
        id=permission.id,
        tenant_id=permission.tenant_id,
        user_id=permission.user_id,
        scope=permission.scope,
        resource_type=permission.resource_type,
        resource_id=permission.resource_id,
        granted_by=permission.granted_by,
        granted_at=permission.granted_at.isoformat(),
        expires_at=permission.expires_at.isoformat() if permission.expires_at else None,
    )


def _parse_expires_at(value: str) -> datetime.datetime:
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise zen(
            "ZEN-PERM-4002",
            "expires_at must be a valid ISO 8601 datetime",
            status_code=400,
            recovery_hint="Use an ISO 8601 timestamp such as 2026-04-07T10:00:00+00:00",
        ) from exc


def _clear_auth_cookie_for_self_permission_mutation(
    response: Response,
    *,
    current_user: dict[str, object],
    target_user_id: str,
) -> None:
    if should_clear_auth_cookie_for_self_target(current_user, target_user_id=target_user_id):
        clear_auth_cookie(response)


async def _record_permission_mutation_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    action: str,
    current_user: dict[str, object],
    permission: Permission,
    revoked_sessions: int,
) -> None:
    actor = resolve_auth_actor(current_user)
    await log_audit(
        db,
        tenant_id=tenant_id,
        action=action,
        result="success",
        user_id=actor.user_id,
        username=actor.username,
        resource_type="permission",
        resource_id=str(permission.id),
        details={
            "target_user_id": permission.user_id,
            "scope": permission.scope,
            "resource_type": permission.resource_type,
            "resource_id": permission.resource_id,
            "revoked_sessions": revoked_sessions,
        },
    )


async def _publish_permission_mutation_event(
    action: str,
    *,
    tenant_id: str,
    current_user: dict[str, object],
    permission: PermissionResponse,
    revoked_sessions: int,
) -> None:
    await publish_control_event(
        CHANNEL_USER_EVENTS,
        action,
        {
            "permission": permission.model_dump(mode="json"),
            "target_user_id": permission.user_id,
            "revoked_sessions": revoked_sessions,
            "actor": build_auth_actor_payload(current_user),
        },
        tenant_id=tenant_id,
    )


@router.post("", response_model=PermissionResponse)
async def grant_permission_endpoint(
    payload: PermissionGrantRequest,
    response: Response,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PermissionResponse:
    """Grant a permission to a user.

    Requires admin privileges.

    Scopes:
    - read:jobs, write:jobs, delete:jobs, admin:jobs
    - read:nodes, write:nodes, admin:nodes
    - read:connectors, write:connectors, admin:connectors
    - read:users, write:users, admin:users

    Permission levels:
    - Global: No resource_type or resource_id
    - Type-level: resource_type set, no resource_id
    - Resource-level: Both resource_type and resource_id set
    """
    tenant_id = require_current_user_tenant_id(current_user)
    granted_by = require_auth_username(current_user)

    expires_at = None
    if payload.expires_at:
        expires_at = _parse_expires_at(payload.expires_at)

    permission = await grant_permission(
        db,
        tenant_id=tenant_id,
        user_id=payload.user_id,
        scope=payload.scope,
        granted_by=granted_by,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        expires_at=expires_at,
    )
    revoked_sessions = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=payload.user_id,
        revoked_by=f"admin:permission_change:{granted_by}",
        redis=None,
    )
    permission_response = _to_response(permission)
    await _record_permission_mutation_audit(
        db,
        tenant_id=tenant_id,
        action="permission.granted",
        current_user=current_user,
        permission=permission,
        revoked_sessions=revoked_sessions,
    )
    await db.commit()
    _clear_auth_cookie_for_self_permission_mutation(
        response,
        current_user=current_user,
        target_user_id=payload.user_id,
    )
    await _publish_permission_mutation_event(
        "permission_granted",
        tenant_id=tenant_id,
        current_user=current_user,
        permission=permission_response,
        revoked_sessions=revoked_sessions,
    )
    return permission_response


@router.delete("/{permission_id}")
async def revoke_permission_endpoint(
    permission_id: int,
    response: Response,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> dict[str, str]:
    """Revoke a permission.

    Requires admin privileges.
    """
    tenant_id = require_current_user_tenant_id(current_user)
    permission = await revoke_permission(db, permission_id, tenant_id=tenant_id)
    revoked_sessions = await revoke_all_user_sessions(
        db,
        tenant_id=tenant_id,
        user_id=permission.user_id,
        revoked_by=f"admin:permission_change:{current_user['username']}",
        redis=None,
    )
    permission_response = _to_response(permission)
    await _record_permission_mutation_audit(
        db,
        tenant_id=tenant_id,
        action="permission.revoked",
        current_user=current_user,
        permission=permission,
        revoked_sessions=revoked_sessions,
    )
    await db.commit()
    _clear_auth_cookie_for_self_permission_mutation(
        response,
        current_user=current_user,
        target_user_id=permission.user_id,
    )
    await _publish_permission_mutation_event(
        "permission_revoked",
        tenant_id=tenant_id,
        current_user=current_user,
        permission=permission_response,
        revoked_sessions=revoked_sessions,
    )
    return {"status": "ok", "message": "Permission revoked"}


@router.get("/users/{user_id}", response_model=list[PermissionResponse])
async def list_user_permissions_endpoint(
    user_id: str,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[PermissionResponse]:
    """List all permissions for a user.

    Requires admin privileges.
    """
    tenant_id = require_current_user_tenant_id(current_user)
    permissions = await list_user_permissions(db, tenant_id=tenant_id, user_id=user_id)
    return [_to_response(p) for p in permissions]
