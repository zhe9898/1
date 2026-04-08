"""User lifecycle management API endpoints.

Provides endpoints for suspending, activating, and deleting users.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin, get_redis, get_tenant_db
from backend.platform.redis.client import RedisClient
from backend.control_plane.admin.user_lifecycle import activate_user, delete_user, suspend_user
from backend.models.user import User

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserSuspendRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class UserResponse(BaseModel):
    id: int
    tenant_id: str
    username: str
    display_name: str | None
    role: str
    status: str
    suspended_at: str | None
    suspended_by: str | None
    suspended_reason: str | None
    deleted_at: str | None
    created_at: str


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        suspended_at=user.suspended_at.isoformat() if user.suspended_at else None,
        suspended_by=user.suspended_by,
        suspended_reason=user.suspended_reason,
        deleted_at=user.deleted_at.isoformat() if user.deleted_at else None,
        created_at=user.created_at.isoformat(),
    )


@router.post("/{user_id}/suspend", response_model=UserResponse)
async def suspend_user_endpoint(
    user_id: int,
    payload: UserSuspendRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> UserResponse:
    """Suspend a user account.

    Requires admin privileges. Suspended users cannot log in.
    All active tokens will be revoked.
    """
    user = await suspend_user(
        db,
        redis,
        tenant_id=current_user["tenant_id"],
        user_id=user_id,
        suspended_by=current_user["username"],
        reason=payload.reason,
    )
    return _to_response(user)


@router.post("/{user_id}/activate", response_model=UserResponse)
async def activate_user_endpoint(
    user_id: int,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> UserResponse:
    """Activate a suspended user account.

    Requires admin privileges. User will be able to log in again.
    """
    user = await activate_user(db, tenant_id=current_user["tenant_id"], user_id=user_id)
    return _to_response(user)


@router.delete("/{user_id}", response_model=UserResponse)
async def delete_user_endpoint(
    user_id: int,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> UserResponse:
    """Soft delete a user account.

    Requires admin privileges. Deleted users cannot be reactivated.
    All active tokens will be revoked.
    """
    user = await delete_user(db, redis, tenant_id=current_user["tenant_id"], user_id=user_id)
    return _to_response(user)

