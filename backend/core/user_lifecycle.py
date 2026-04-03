"""User lifecycle management helpers.

Provides functions for suspending, activating, and deleting users,
with automatic token revocation.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import zen
from backend.core.sessions import revoke_all_user_sessions
from backend.models.user import User

if TYPE_CHECKING:
    from backend.core.redis_client import RedisClient


async def suspend_user(
    db: AsyncSession,
    redis: RedisClient | None,
    user_id: int,
    suspended_by: str,
    reason: str | None = None,
) -> User:
    """Suspend a user and revoke all their tokens.

    Args:
        db: Database session
        redis: Redis client for token blacklist
        user_id: User ID to suspend
        suspended_by: Username of admin performing suspension
        reason: Optional reason for suspension

    Returns:
        Updated user object

    Raises:
        HTTPException: If user not found or already suspended
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()

    if user is None:
        raise zen("ZEN-AUTH-4040", "User not found", status_code=404)

    if user.status == "suspended":
        raise zen("ZEN-AUTH-4090", "User is already suspended", status_code=409)

    if user.status == "deleted":
        raise zen("ZEN-AUTH-4090", "User is deleted", status_code=409)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    user.status = "suspended"
    user.suspended_at = now
    user.suspended_by = suspended_by
    user.suspended_reason = reason
    user.is_active = False

    await db.flush()

    await revoke_all_user_sessions(
        db,
        tenant_id=user.tenant_id,
        user_id=str(user.id),
        revoked_by=f"admin:suspend:{suspended_by}",
        redis=redis,
    )

    return cast("User", user)


async def activate_user(
    db: AsyncSession,
    user_id: int,
) -> User:
    """Activate a suspended user.

    Args:
        db: Database session
        user_id: User ID to activate

    Returns:
        Updated user object

    Raises:
        HTTPException: If user not found or not suspended
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()

    if user is None:
        raise zen("ZEN-AUTH-4040", "User not found", status_code=404)

    if user.status == "active":
        raise zen("ZEN-AUTH-4090", "User is already active", status_code=409)

    if user.status == "deleted":
        raise zen("ZEN-AUTH-4090", "Cannot activate deleted user", status_code=409)

    user.status = "active"
    user.suspended_at = None
    user.suspended_by = None
    user.suspended_reason = None
    user.is_active = True

    await db.flush()

    return cast("User", user)


async def delete_user(
    db: AsyncSession,
    redis: RedisClient | None,
    user_id: int,
) -> User:
    """Soft delete a user and revoke all their tokens.

    Args:
        db: Database session
        redis: Redis client for token blacklist
        user_id: User ID to delete

    Returns:
        Updated user object

    Raises:
        HTTPException: If user not found or already deleted
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()

    if user is None:
        raise zen("ZEN-AUTH-4040", "User not found", status_code=404)

    if user.status == "deleted":
        raise zen("ZEN-AUTH-4090", "User is already deleted", status_code=409)

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    user.status = "deleted"
    user.deleted_at = now
    user.is_active = False

    await db.flush()

    await revoke_all_user_sessions(
        db,
        tenant_id=user.tenant_id,
        user_id=str(user.id),
        revoked_by="admin:delete_user",
        redis=redis,
    )

    return cast("User", user)
