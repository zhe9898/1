"""Quota enforcement helpers."""

from __future__ import annotations

from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import zen
from backend.models.connector import Connector
from backend.models.job import Job
from backend.models.node import Node
from backend.models.quota import DEFAULT_QUOTAS, Quota


async def _get_limit(db: AsyncSession, tenant_id: str, resource_type: str) -> int:
    """Get quota limit for tenant+resource. Returns DEFAULT_QUOTAS if not set."""
    result = await db.execute(
        select(Quota).where(
            Quota.tenant_id == tenant_id,
            Quota.resource_type == resource_type,
        )
    )
    quota = result.scalars().first()
    if quota is None:
        return DEFAULT_QUOTAS.get(resource_type, -1)
    return cast(int, quota.limit)


async def check_node_quota(db: AsyncSession, tenant_id: str) -> None:
    """Raise ZEN-QUOTA-4290 if tenant is at node limit."""
    limit = await _get_limit(db, tenant_id, "nodes")
    if limit == -1:
        return
    count_result = await db.execute(
        select(func.count()).where(
            Node.tenant_id == tenant_id,
            Node.enrollment_status.in_(("pending", "active")),
        )
    )
    used = count_result.scalar() or 0
    if used >= limit:
        raise zen(
            "ZEN-QUOTA-4290",
            f"Node quota exceeded ({used}/{limit})",
            status_code=429,
            recovery_hint="Remove unused nodes or contact admin to increase quota",
            details={"resource": "nodes", "used": used, "limit": limit},
        )


async def check_connector_quota(db: AsyncSession, tenant_id: str) -> None:
    """Raise ZEN-QUOTA-4290 if tenant is at connector limit."""
    limit = await _get_limit(db, tenant_id, "connectors")
    if limit == -1:
        return
    count_result = await db.execute(select(func.count()).where(Connector.tenant_id == tenant_id))
    used = count_result.scalar() or 0
    if used >= limit:
        raise zen(
            "ZEN-QUOTA-4290",
            f"Connector quota exceeded ({used}/{limit})",
            status_code=429,
            recovery_hint="Remove unused connectors or contact admin to increase quota",
            details={"resource": "connectors", "used": used, "limit": limit},
        )


async def check_concurrent_job_quota(db: AsyncSession, tenant_id: str) -> None:
    """Raise ZEN-QUOTA-4290 if too many jobs are leased concurrently."""
    limit = await _get_limit(db, tenant_id, "jobs_concurrent")
    if limit == -1:
        return
    count_result = await db.execute(
        select(func.count()).where(
            Job.tenant_id == tenant_id,
            Job.status == "leased",
        )
    )
    used = count_result.scalar() or 0
    if used >= limit:
        raise zen(
            "ZEN-QUOTA-4290",
            f"Concurrent job quota exceeded ({used}/{limit})",
            status_code=429,
            recovery_hint="Wait for running jobs to complete or contact admin to increase quota",
            details={"resource": "jobs_concurrent", "used": used, "limit": limit},
        )


async def check_per_kind_quota(db: AsyncSession, tenant_id: str, kind: str) -> None:
    """Raise ZEN-QUOTA-4290 if too many concurrent jobs of a specific kind.

    Checks two layers:
    1. Per-kind specific limit (resource_type = ``jobs_per_kind:<kind>``)
    2. Global per-kind default (resource_type = ``jobs_per_kind``)

    A limit of -1 means unlimited (no cap).
    """
    # Try kind-specific override first, then fall back to generic per-kind limit
    specific_key = f"jobs_per_kind:{kind}"
    limit = await _get_limit(db, tenant_id, specific_key)
    if limit == -1:
        # No specific override → try generic per-kind default
        limit = await _get_limit(db, tenant_id, "jobs_per_kind")
    if limit == -1:
        return

    count_result = await db.execute(
        select(func.count()).where(
            Job.tenant_id == tenant_id,
            Job.kind == kind,
            Job.status == "leased",
        )
    )
    used = count_result.scalar() or 0
    if used >= limit:
        raise zen(
            "ZEN-QUOTA-4290",
            f"Per-kind quota exceeded for '{kind}' ({used}/{limit})",
            status_code=429,
            recovery_hint=f"Wait for running '{kind}' jobs to complete or contact admin to increase quota",
            details={"resource": specific_key, "kind": kind, "used": used, "limit": limit},
        )


async def get_quota_status(db: AsyncSession, tenant_id: str) -> dict[str, dict[str, int]]:
    """Return current usage vs limit for all quota types."""
    # Collect live counts
    node_count = (await db.execute(select(func.count()).where(Node.tenant_id == tenant_id, Node.enrollment_status.in_(("pending", "active"))))).scalar() or 0

    connector_count = (await db.execute(select(func.count()).where(Connector.tenant_id == tenant_id))).scalar() or 0

    job_concurrent = (await db.execute(select(func.count()).where(Job.tenant_id == tenant_id, Job.status == "leased"))).scalar() or 0

    result: dict[str, dict[str, int]] = {}
    for resource_type, used in [
        ("nodes", node_count),
        ("connectors", connector_count),
        ("jobs_concurrent", job_concurrent),
    ]:
        limit = await _get_limit(db, tenant_id, resource_type)
        result[resource_type] = {"used": used, "limit": limit}

    # Per-kind breakdown: show usage for each kind that has active leases
    kind_counts_result = await db.execute(
        select(Job.kind, func.count())
        .where(
            Job.tenant_id == tenant_id,
            Job.status == "leased",
        )
        .group_by(Job.kind)
    )
    for kind, count in kind_counts_result.all():
        if kind:
            specific_key = f"jobs_per_kind:{kind}"
            limit = await _get_limit(db, tenant_id, specific_key)
            if limit == -1:
                limit = await _get_limit(db, tenant_id, "jobs_per_kind")
            result[specific_key] = {"used": count, "limit": limit}

    return result


async def set_quota(
    db: AsyncSession,
    *,
    tenant_id: str,
    resource_type: str,
    limit: int,
    updated_by: str,
) -> Quota:
    """Set or update a quota for a tenant+resource."""
    result = await db.execute(
        select(Quota).where(
            Quota.tenant_id == tenant_id,
            Quota.resource_type == resource_type,
        )
    )
    quota = result.scalars().first()
    if quota is None:
        quota = Quota(tenant_id=tenant_id, resource_type=resource_type, limit=limit, updated_by=updated_by)
        db.add(quota)
    else:
        quota.limit = limit
        quota.updated_by = updated_by
    await db.flush()
    return cast("Quota", quota)
