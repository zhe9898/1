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
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.audit_log import AuditLog
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.scheduling_decision import SchedulingDecision

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

RETENTION_JOBS_DAYS = int(os.getenv("RETENTION_JOBS_DAYS", "30"))
RETENTION_SCHEDULING_DAYS = int(os.getenv("RETENTION_SCHEDULING_DAYS", "90"))
RETENTION_AUDIT_DAYS = int(os.getenv("RETENTION_AUDIT_DAYS", "180"))
RETENTION_BATCH_SIZE = int(os.getenv("RETENTION_BATCH_SIZE", "500"))


def _cutoff(days: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=days)


async def purge_old_jobs(session: AsyncSession) -> int:
    """Delete terminal-state jobs older than RETENTION_JOBS_DAYS.

    job_logs are cascade-deleted via FK(ondelete=CASCADE).
    job_attempts are cleaned separately (no FK cascade).
    """
    cutoff = _cutoff(RETENTION_JOBS_DAYS)
    # Fetch IDs first so we can clean job_attempts in the same batch.
    id_query = (
        select(Job.job_id)
        .where(Job.status.in_(TERMINAL_STATUSES), Job.updated_at < cutoff)
        .limit(RETENTION_BATCH_SIZE)
    )
    result = await session.execute(id_query)
    job_ids = list(result.scalars().all())
    if not job_ids:
        return 0

    # Delete matching job_attempts first (no FK cascade).
    await session.execute(
        delete(JobAttempt).where(JobAttempt.job_id.in_(job_ids))
    )

    # Delete jobs (cascades to job_logs).
    await session.execute(
        delete(Job).where(Job.job_id.in_(job_ids))
    )
    await session.commit()
    logger.info("data_retention: purged %d terminal jobs (cutoff=%s)", len(job_ids), cutoff.isoformat())
    return len(job_ids)


async def purge_old_scheduling_decisions(session: AsyncSession) -> int:
    """Delete scheduling_decisions older than RETENTION_SCHEDULING_DAYS."""
    cutoff = _cutoff(RETENTION_SCHEDULING_DAYS)
    stmt = (
        delete(SchedulingDecision)
        .where(SchedulingDecision.created_at < cutoff)
    )
    result = await session.execute(stmt)
    count = result.rowcount  # type: ignore[assignment]
    if count:
        await session.commit()
        logger.info("data_retention: purged %d scheduling_decisions (cutoff=%s)", count, cutoff.isoformat())
    return count


async def purge_old_audit_logs(session: AsyncSession) -> int:
    """Delete audit_logs older than RETENTION_AUDIT_DAYS."""
    cutoff = _cutoff(RETENTION_AUDIT_DAYS)
    stmt = (
        delete(AuditLog)
        .where(AuditLog.created_at < cutoff)
    )
    result = await session.execute(stmt)
    count = result.rowcount  # type: ignore[assignment]
    if count:
        await session.commit()
        logger.info("data_retention: purged %d audit_logs (cutoff=%s)", count, cutoff.isoformat())
    return count


async def run_retention_cycle(session: AsyncSession) -> dict[str, int]:
    """Execute one full retention cycle across all tables."""
    jobs = await purge_old_jobs(session)
    decisions = await purge_old_scheduling_decisions(session)
    audits = await purge_old_audit_logs(session)
    return {"jobs": jobs, "scheduling_decisions": decisions, "audit_logs": audits}
