"""Data retention — automatic cleanup of aged-out records.

Tables and default retention periods:
- jobs (terminal states only): 30 days  → cascades to job_logs via FK
- job_attempts (matching cleaned jobs): 30 days
- scheduling_decisions: 90 days
- audit_logs: 180 days

Environment variables:
- RETENTION_JOBS_DAYS          (default 30)
- RETENTION_SCHEDULING_DAYS    (default 90)
- RETENTION_AUDIT_DAYS         (default 180)
- RETENTION_BATCH_SIZE         (default 500)

Safety invariants:
- Only deletes terminal-state jobs (completed / failed / cancelled).
- Batch deletes capped by RETENTION_BATCH_SIZE to avoid long txns.
"""

from __future__ import annotations

import datetime
import logging
import os

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.rls import set_tenant_context
from backend.models.audit_log import AuditLog
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.scheduling_decision import SchedulingDecision
from backend.models.tenant import Tenant

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

RETENTION_JOBS_DAYS = int(os.getenv("RETENTION_JOBS_DAYS", "30"))
RETENTION_SCHEDULING_DAYS = int(os.getenv("RETENTION_SCHEDULING_DAYS", "90"))
RETENTION_AUDIT_DAYS = int(os.getenv("RETENTION_AUDIT_DAYS", "180"))
RETENTION_BATCH_SIZE = int(os.getenv("RETENTION_BATCH_SIZE", "500"))


def _cutoff(days: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=days)


async def purge_old_jobs(session: AsyncSession, tenant_id: str) -> int:
    """Delete terminal-state jobs older than RETENTION_JOBS_DAYS for one tenant.

    job_logs are cascade-deleted via FK(ondelete=CASCADE).
    job_attempts are cleaned separately (no FK cascade).
    """
    await set_tenant_context(session, tenant_id)
    cutoff = _cutoff(RETENTION_JOBS_DAYS)
    # Fetch IDs first so we can clean job_attempts in the same batch.
    id_query = (
        select(Job.job_id)
        .where(
            Job.tenant_id == tenant_id,
            Job.status.in_(TERMINAL_STATUSES),
            Job.updated_at < cutoff,
        )
        .limit(RETENTION_BATCH_SIZE)
    )
    result = await session.execute(id_query)
    job_ids = list(result.scalars().all())
    if not job_ids:
        return 0

    # Delete matching job_attempts first (no FK cascade).
    await session.execute(
        delete(JobAttempt).where(
            JobAttempt.tenant_id == tenant_id,
            JobAttempt.job_id.in_(job_ids),
        )
    )

    # Delete jobs (cascades to job_logs).
    await session.execute(delete(Job).where(Job.tenant_id == tenant_id, Job.job_id.in_(job_ids)))
    await session.commit()
    logger.info("data_retention: purged %d terminal jobs (tenant=%s cutoff=%s)", len(job_ids), tenant_id, cutoff.isoformat())
    return len(job_ids)


async def purge_old_scheduling_decisions(session: AsyncSession, tenant_id: str) -> int:
    """Delete scheduling_decisions older than RETENTION_SCHEDULING_DAYS for one tenant."""
    await set_tenant_context(session, tenant_id)
    cutoff = _cutoff(RETENTION_SCHEDULING_DAYS)
    stmt = delete(SchedulingDecision).where(
        SchedulingDecision.tenant_id == tenant_id,
        SchedulingDecision.created_at < cutoff,
    )
    result = await session.execute(stmt)
    count = result.rowcount
    if count:
        await session.commit()
        logger.info(
            "data_retention: purged %d scheduling_decisions (tenant=%s cutoff=%s)",
            count,
            tenant_id,
            cutoff.isoformat(),
        )
    return int(count)


async def purge_old_audit_logs(session: AsyncSession, tenant_id: str) -> int:
    """Delete audit_logs older than RETENTION_AUDIT_DAYS for one tenant."""
    await set_tenant_context(session, tenant_id)
    cutoff = _cutoff(RETENTION_AUDIT_DAYS)
    stmt = delete(AuditLog).where(
        AuditLog.tenant_id == tenant_id,
        AuditLog.created_at < cutoff,
    )
    result = await session.execute(stmt)
    count = result.rowcount
    if count:
        await session.commit()
        logger.info("data_retention: purged %d audit_logs (tenant=%s cutoff=%s)", count, tenant_id, cutoff.isoformat())
    return int(count)


async def _list_active_tenant_ids(session: AsyncSession) -> list[str]:
    """Return active tenants for retention sweeps.

    Falls back to the default tenant when tenant metadata is unavailable.
    """
    try:
        result = await session.execute(select(Tenant.tenant_id).where(Tenant.is_active.is_(True)).order_by(Tenant.tenant_id.asc()))
        tenant_ids = [tenant_id for tenant_id in result.scalars().all() if tenant_id]
    except (SQLAlchemyError, RuntimeError, OSError, TypeError, ValueError) as exc:
        logger.warning("data_retention: failed to list active tenants; falling back to default tenant: %s", exc)
        return ["default"]
    return tenant_ids or ["default"]


async def _run_retention_cycle_for_tenant(session: AsyncSession, tenant_id: str) -> dict[str, int]:
    jobs = await purge_old_jobs(session, tenant_id)
    decisions = await purge_old_scheduling_decisions(session, tenant_id)
    audits = await purge_old_audit_logs(session, tenant_id)
    return {"jobs": jobs, "scheduling_decisions": decisions, "audit_logs": audits}


async def run_retention_cycle(session: AsyncSession) -> dict[str, int]:
    """Execute one full retention cycle across all active tenants."""
    totals = {"jobs": 0, "scheduling_decisions": 0, "audit_logs": 0}
    tenant_ids = await _list_active_tenant_ids(session)
    for tenant_id in tenant_ids:
        summary = await _run_retention_cycle_for_tenant(session, tenant_id)
        totals["jobs"] += summary["jobs"]
        totals["scheduling_decisions"] += summary["scheduling_decisions"]
        totals["audit_logs"] += summary["audit_logs"]
    return totals
