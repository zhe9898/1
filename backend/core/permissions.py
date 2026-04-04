"""Permission management helpers for fine-grained access control."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import zen
from backend.models.permission import Permission

if TYPE_CHECKING:
    pass


async def grant_permission(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    scope: str,
    granted_by: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    expires_at: datetime.datetime | None = None,
) -> Permission:
    """Grant a permission to a user.

    Args:
        db: Database session
        tenant_id: Tenant ID
        user_id: User ID to grant permission to
        scope: Permission scope (read:jobs, write:nodes, etc.)
        granted_by: Username of admin granting permission
        resource_type: Optional resource type (jobs, nodes, etc.)
        resource_id: Optional specific resource ID
        expires_at: Optional expiration date

    Returns:
        Created permission object
    """
    # Check if permission already exists
    result = await db.execute(
        select(Permission).where(
            and_(
                Permission.tenant_id == tenant_id,
                Permission.user_id == user_id,
                Permission.scope == scope,
                Permission.resource_type == resource_type,
                Permission.resource_id == resource_id,
            )
        )
    )
    existing = result.scalars().first()

    if existing:
        raise zen("ZEN-PERM-4090", "Permission already exists", status_code=409)

    permission = Permission(
        tenant_id=tenant_id,
        user_id=user_id,
        scope=scope,
        resource_type=resource_type,
        resource_id=resource_id,
        granted_by=granted_by,
        granted_at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        expires_at=expires_at,
    )
    db.add(permission)
    await db.flush()
    return permission


async def revoke_permission(
    db: AsyncSession,
    permission_id: int,
) -> None:
    """Revoke a permission.

    Args:
        db: Database session
        permission_id: Permission ID to revoke
    """
    result = await db.execute(select(Permission).where(Permission.id == permission_id))
    permission = result.scalars().first()

    if permission is None:
        raise zen("ZEN-PERM-4040", "Permission not found", status_code=404)

    await db.delete(permission)
    await db.flush()


async def check_permission(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    scope: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> bool:
    """Check if user has a specific permission.

    Checks in order:
    1. Specific resource permission (resource_type + resource_id)
    2. Type-level permission (resource_type, no resource_id)
    3. Global permission (no resource_type, no resource_id)

    Args:
        db: Database session
        tenant_id: Tenant ID
        user_id: User ID
        scope: Permission scope
        resource_type: Optional resource type
        resource_id: Optional resource ID

    Returns:
        True if user has permission, False otherwise
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    # Build query conditions
    conditions = [
        Permission.tenant_id == tenant_id,
        Permission.user_id == user_id,
        Permission.scope == scope,
        or_(Permission.expires_at.is_(None), Permission.expires_at > now),
    ]

    # Check specific resource permission
    if resource_type and resource_id:
        result = await db.execute(
            select(Permission).where(
                and_(
                    *conditions,
                    Permission.resource_type == resource_type,
                    Permission.resource_id == resource_id,
                )
            )
        )
        if result.scalars().first():
            return True

    # Check type-level permission
    if resource_type:
        result = await db.execute(
            select(Permission).where(
                and_(
                    *conditions,
                    Permission.resource_type == resource_type,
                    Permission.resource_id.is_(None),
                )
            )
        )
        if result.scalars().first():
            return True

    # Check global permission
    result = await db.execute(
        select(Permission).where(
            and_(
                *conditions,
                Permission.resource_type.is_(None),
                Permission.resource_id.is_(None),
            )
        )
    )
    return result.scalars().first() is not None


async def list_user_permissions(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
) -> list[Permission]:
    """List all permissions for a user.

    Args:
        db: Database session
        tenant_id: Tenant ID
        user_id: User ID

    Returns:
        List of permission objects
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    result = await db.execute(
        select(Permission).where(
            and_(
                Permission.tenant_id == tenant_id,
                Permission.user_id == user_id,
                or_(Permission.expires_at.is_(None), Permission.expires_at > now),
            )
        )
    )
    return list(result.scalars().all())


async def get_user_scopes(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
) -> list[str]:
    """Get all scopes for a user (for JWT).

    Args:
        db: Database session
        tenant_id: Tenant ID
        user_id: User ID

    Returns:
        List of scope strings
    """
    permissions = await list_user_permissions(db, tenant_id=tenant_id, user_id=user_id)
    scopes: set[str] = set()
    for permission in permissions:
        scope = getattr(permission, "scope", None)
        if isinstance(scope, str):
            normalized = scope.strip()
            if normalized:
                scopes.add(normalized)
    return list(scopes)
