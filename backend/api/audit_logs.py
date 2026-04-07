"""Audit log query API endpoints."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin, get_tenant_db
from backend.models.audit_log import AuditLog

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])


class AuditLogResponse(BaseModel):
    id: int
    tenant_id: str
    user_id: str | None
    username: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    ip_address: str | None
    user_agent: str | None
    result: str
    error_code: str | None
    error_message: str | None
    details: dict
    created_at: str


def _to_response(log: AuditLog) -> AuditLogResponse:
    return AuditLogResponse(
        id=log.id,
        tenant_id=log.tenant_id,
        user_id=log.user_id,
        username=log.username,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        result=log.result,
        error_code=log.error_code,
        error_message=log.error_message,
        details=log.details,
        created_at=log.created_at.isoformat(),
    )


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    user_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    result: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[AuditLogResponse]:
    """Query audit logs.

    Requires admin privileges. Supports filtering by:
    - user_id: Filter by user
    - action: Filter by action (login, create_job, etc.)
    - resource_type: Filter by resource type (user, job, node, etc.)
    - resource_id: Filter by specific resource
    - result: Filter by result (success, failure)
    - start_date: Filter by start date (ISO format)
    - end_date: Filter by end date (ISO format)
    - limit: Maximum number of results (default 100, max 1000)

    Returns logs in reverse chronological order (newest first).
    """
    tenant_id = current_user["tenant_id"]

    # Build query
    query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)

    if user_id:
        query = query.where(AuditLog.user_id == user_id)

    if action:
        query = query.where(AuditLog.action == action)

    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)

    if resource_id:
        query = query.where(AuditLog.resource_id == resource_id)

    if result:
        query = query.where(AuditLog.result == result)

    if start_date:
        start_dt = datetime.datetime.fromisoformat(start_date)
        query = query.where(AuditLog.created_at >= start_dt)

    if end_date:
        end_dt = datetime.datetime.fromisoformat(end_date)
        query = query.where(AuditLog.created_at <= end_dt)

    # Order by newest first and limit
    query = query.order_by(desc(AuditLog.created_at)).limit(limit)

    db_result = await db.execute(query)
    logs = db_result.scalars().all()

    return [_to_response(log) for log in logs]
