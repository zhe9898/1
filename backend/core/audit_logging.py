"""Audit logging helpers for recording security and operational events."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.audit_log import AuditLog

if TYPE_CHECKING:
    from fastapi import Request


async def log_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    action: str,
    result: str,
    user_id: str | None = None,
    username: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    details: dict | None = None,
) -> AuditLog:
    """Create an audit log entry.

    Args:
        db: Database session
        tenant_id: Tenant ID
        action: Action performed (login, create_job, suspend_user, etc.)
        result: Result of action (success, failure)
        user_id: User ID (None for system actions)
        username: Username
        resource_type: Type of resource (user, job, node, etc.)
        resource_id: ID of resource
        ip_address: Client IP address
        user_agent: Client user agent
        error_code: Error code if failed
        error_message: Error message if failed
        details: Additional context

    Returns:
        Created audit log entry
    """
    log = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        user_agent=user_agent,
        result=result,
        error_code=error_code,
        error_message=error_message,
        details=details or {},
        created_at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
    )
    db.add(log)
    await db.flush()
    return log


def extract_client_info(request: Request) -> tuple[str | None, str | None]:
    """Extract client IP and user agent from request.

    Args:
        request: FastAPI request object

    Returns:
        Tuple of (ip_address, user_agent)
    """
    # Try to get real IP from X-Forwarded-For header
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else None

    user_agent = request.headers.get("User-Agent")

    return ip_address, user_agent
