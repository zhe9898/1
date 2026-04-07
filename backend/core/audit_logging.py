"""Audit logging helpers for recording security and operational events."""

from __future__ import annotations

import datetime
import ipaddress
import os
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


def _is_trusted_proxy(source_ip: str | None) -> bool:
    if not source_ip:
        return False
    trusted = os.getenv("TRUSTED_PROXY_CIDRS", "").strip()
    if not trusted:
        return False
    try:
        source = ipaddress.ip_address(source_ip)
    except ValueError:
        return False
    for raw in trusted.split(","):
        cidr = raw.strip()
        if not cidr:
            continue
        try:
            if source in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def extract_client_info(request: Request) -> tuple[str | None, str | None]:
    """Extract client IP and user agent from request.

    Args:
        request: FastAPI request object

    Returns:
        Tuple of (ip_address, user_agent)
    """
    # Try to get real IP from X-Forwarded-For header
    client_host = request.client.host if request.client else ""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for and _is_trusted_proxy(client_host):
        candidate = forwarded_for.split(",")[0].strip()
        try:
            ip_address = str(ipaddress.ip_address(candidate))
        except ValueError:
            ip_address = client_host
    else:
        ip_address = client_host

    user_agent = request.headers.get("User-Agent")

    return ip_address, user_agent
