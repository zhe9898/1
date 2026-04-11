"""Permission management API endpoints."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_admin, get_tenant_db
from backend.control_plane.auth.permissions import ALLOWED_SCOPES, grant_permission, is_valid_scope, list_user_permissions, normalize_scope, revoke_permission
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.permission import Permission

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


@router.post("", response_model=PermissionResponse)
async def grant_permission_endpoint(
    payload: PermissionGrantRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
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
    granted_by = current_user["username"]

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

    return _to_response(permission)


@router.delete("/{permission_id}")
async def revoke_permission_endpoint(
    permission_id: int,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Revoke a permission.

    Requires admin privileges.
    """
    tenant_id = require_current_user_tenant_id(current_user)
    await revoke_permission(db, permission_id, tenant_id=tenant_id)
    return {"status": "ok", "message": "Permission revoked"}


@router.get("/users/{user_id}", response_model=list[PermissionResponse])
async def list_user_permissions_endpoint(
    user_id: str,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[PermissionResponse]:
    """List all permissions for a user.

    Requires admin privileges.
    """
    tenant_id = require_current_user_tenant_id(current_user)
    permissions = await list_user_permissions(db, tenant_id=tenant_id, user_id=user_id)
    return [_to_response(p) for p in permissions]
