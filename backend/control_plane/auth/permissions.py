"""Permission management helpers for fine-grained access control."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.role_claims import has_admin_role_value
from backend.models.permission import Permission
from backend.models.user import User

if TYPE_CHECKING:
    pass


# Canonical scope allow-list for JWT claims and grant APIs.
ALLOWED_SCOPES: frozenset[str] = frozenset(
    {
        "read:jobs",
        "write:jobs",
        "admin:jobs",
        "read:nodes",
        "write:nodes",
        "admin:nodes",
        "read:connectors",
        "write:connectors",
        "admin:connectors",
        "read:users",
        "write:users",
        "admin:users",
        "admin:quotas",
        "admin:alerts",
        "admin:audit",
    }
)


def normalize_scope(scope: str) -> str:
    return scope.strip().lower()


def is_valid_scope(scope: str) -> bool:
    return normalize_scope(scope) in ALLOWED_SCOPES


def filter_valid_scopes(scopes: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    if not scopes:
        return []
    normalized = {normalize_scope(scope) for scope in scopes if isinstance(scope, str) and scope.strip()}
    return sorted(scope for scope in normalized if scope in ALLOWED_SCOPES)


def hydrate_scopes_for_role(scopes: list[str] | tuple[str, ...] | set[str] | None, role: str | None) -> list[str]:
    effective_scopes = set(filter_valid_scopes(scopes))
    if has_admin_role_value(role):
        effective_scopes.update(ALLOWED_SCOPES)
    return sorted(effective_scopes)


def assert_valid_scope(scope: str) -> str:
    normalized = normalize_scope(scope)
    if normalized not in ALLOWED_SCOPES:
        raise zen(
            "ZEN-PERM-4001",
            "Invalid permission scope",
            status_code=400,
            recovery_hint="Use one of the supported permission scopes",
            details={"scope": scope, "allowed_scopes": sorted(ALLOWED_SCOPES)},
        )
    return normalized


def _utcnow_naive() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _normalize_permission_user_id(user_id: str) -> tuple[str, int]:
    normalized = str(user_id).strip()
    if not normalized or not normalized.isdigit():
        raise zen(
            "ZEN-PERM-4003",
            "user_id must be a numeric user identifier",
            status_code=400,
            recovery_hint="Use the numeric user ID returned by the user management APIs",
        )
    return normalized, int(normalized)


def normalize_permission_expiry(expires_at: datetime.datetime | None) -> datetime.datetime | None:
    if expires_at is None:
        return None
    if expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(datetime.UTC).replace(tzinfo=None)
    if expires_at <= _utcnow_naive():
        raise zen(
            "ZEN-PERM-4002",
            "expires_at must be a future datetime",
            status_code=400,
            recovery_hint="Provide an expiration timestamp later than the current UTC time",
        )
    return expires_at


async def ensure_permission_target_user(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
) -> str:
    normalized_user_id, parsed_user_id = _normalize_permission_user_id(user_id)
    result = await db.execute(
        select(User).where(
            User.id == parsed_user_id,
            User.tenant_id == tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise zen(
            "ZEN-PERM-4004",
            "Target user not found in this tenant",
            status_code=404,
            recovery_hint="Look up the user from the current tenant before granting permissions",
        )
    return normalized_user_id


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
    validated_scope = assert_valid_scope(scope)
    normalized_expires_at = normalize_permission_expiry(expires_at)
    normalized_user_id = await ensure_permission_target_user(db, tenant_id=tenant_id, user_id=user_id)

    # Check if permission already exists
    result = await db.execute(
        select(Permission).where(
            and_(
                Permission.tenant_id == tenant_id,
                Permission.user_id == normalized_user_id,
                Permission.scope == validated_scope,
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
        user_id=normalized_user_id,
        scope=validated_scope,
        resource_type=resource_type,
        resource_id=resource_id,
        granted_by=granted_by,
        granted_at=_utcnow_naive(),
        expires_at=normalized_expires_at,
    )
    db.add(permission)
    await db.flush()
    return permission


async def revoke_permission(
    db: AsyncSession,
    permission_id: int,
    *,
    tenant_id: str,
) -> None:
    """Revoke a permission.

    Args:
        db: Database session
        permission_id: Permission ID to revoke
    """
    result = await db.execute(
        select(Permission).where(
            Permission.id == permission_id,
            Permission.tenant_id == tenant_id,
        )
    )
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
    validated_scope = assert_valid_scope(scope)
    now = _utcnow_naive()

    # Build query conditions
    conditions = [
        Permission.tenant_id == tenant_id,
        Permission.user_id == user_id,
        Permission.scope == validated_scope,
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
    now = _utcnow_naive()

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
    scopes = [getattr(permission, "scope", "") for permission in permissions if isinstance(getattr(permission, "scope", None), str)]
    return filter_valid_scopes(scopes)
