"""User lifecycle management API endpoints.

Provides endpoints for suspending, activating, and deleting users.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.auth_shared import build_auth_actor_payload, require_auth_username, resolve_auth_actor
from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.deps import get_current_admin, get_redis, get_tenant_db
from backend.control_plane.admin.user_lifecycle import activate_user, delete_user, suspend_user
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.user import User
from backend.platform.logging.audit import log_audit
from backend.platform.redis.client import CHANNEL_USER_EVENTS, RedisClient

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


async def _record_user_lifecycle_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    action: str,
    current_user: dict[str, object],
    user: User,
    reason: str | None = None,
) -> None:
    actor = resolve_auth_actor(current_user)
    details = {
        "target_user_id": str(user.id),
        "target_username": user.username,
        "target_status": user.status,
    }
    if reason is not None:
        details["reason"] = reason
    await log_audit(
        db,
        tenant_id=tenant_id,
        action=f"user.{action}",
        result="success",
        user_id=actor.user_id,
        username=actor.username,
        resource_type="user",
        resource_id=str(user.id),
        details=details,
    )


async def _publish_user_lifecycle_event(
    action: str,
    *,
    tenant_id: str,
    current_user: dict[str, object],
    user_response: UserResponse,
) -> None:
    await publish_control_event(
        CHANNEL_USER_EVENTS,
        action,
        {
            "user": user_response.model_dump(mode="json"),
            "actor": build_auth_actor_payload(current_user),
        },
        tenant_id=tenant_id,
    )


@router.post("/{user_id}/suspend", response_model=UserResponse)
async def suspend_user_endpoint(
    user_id: int,
    payload: UserSuspendRequest,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> UserResponse:
    """Suspend a user account.

    Requires admin privileges. Suspended users cannot log in.
    All active tokens will be revoked.
    """
    tenant_id = require_current_user_tenant_id(current_user)
    user = await suspend_user(
        db,
        redis,
        tenant_id=tenant_id,
        user_id=user_id,
        suspended_by=require_auth_username(current_user),
        reason=payload.reason,
    )
    response = _to_response(user)
    await _record_user_lifecycle_audit(
        db,
        tenant_id=tenant_id,
        action="suspended",
        current_user=current_user,
        user=user,
        reason=payload.reason,
    )
    await db.commit()
    await _publish_user_lifecycle_event(
        "suspended",
        tenant_id=tenant_id,
        current_user=current_user,
        user_response=response,
    )
    return response


@router.post("/{user_id}/activate", response_model=UserResponse)
async def activate_user_endpoint(
    user_id: int,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> UserResponse:
    """Activate a suspended user account.

    Requires admin privileges. User will be able to log in again.
    """
    tenant_id = require_current_user_tenant_id(current_user)
    user = await activate_user(db, tenant_id=tenant_id, user_id=user_id)
    response = _to_response(user)
    await _record_user_lifecycle_audit(
        db,
        tenant_id=tenant_id,
        action="activated",
        current_user=current_user,
        user=user,
    )
    await db.commit()
    await _publish_user_lifecycle_event(
        "activated",
        tenant_id=tenant_id,
        current_user=current_user,
        user_response=response,
    )
    return response


@router.delete("/{user_id}", response_model=UserResponse)
async def delete_user_endpoint(
    user_id: int,
    current_user: Annotated[dict[str, object], Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    redis: Annotated[RedisClient | None, Depends(get_redis)],
) -> UserResponse:
    """Soft delete a user account.

    Requires admin privileges. Deleted users cannot be reactivated.
    All active tokens will be revoked.
    """
    tenant_id = require_current_user_tenant_id(current_user)
    user = await delete_user(db, redis, tenant_id=tenant_id, user_id=user_id)
    response = _to_response(user)
    await _record_user_lifecycle_audit(
        db,
        tenant_id=tenant_id,
        action="deleted",
        current_user=current_user,
        user=user,
    )
    await db.commit()
    await _publish_user_lifecycle_event(
        "deleted",
        tenant_id=tenant_id,
        current_user=current_user,
        user_response=response,
    )
    return response
