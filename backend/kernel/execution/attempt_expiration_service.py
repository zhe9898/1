from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.execution.job_lifecycle_service import JobLifecycleService
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class AttemptExpirationSweepResult:
    inspected: int
    requeued: int
    repaired_without_attempt: int


async def expire_stale_attempts(
    db: AsyncSession,
    *,
    now: datetime.datetime | None = None,
    limit: int = 100,
) -> AttemptExpirationSweepResult:
    sweep_now = now or _utcnow()
    result = await db.execute(
        select(Job)
        .where(
            Job.status == "leased",
            Job.leased_until.is_not(None),
            Job.leased_until < sweep_now,
        )
        .order_by(Job.leased_until.asc(), Job.updated_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    stale_jobs = list(result.scalars().all())
    repaired_without_attempt = 0
    for job in stale_jobs:
        attempt = await _load_current_attempt(db, job)
        if attempt is None:
            repaired_without_attempt += 1
        await JobLifecycleService.expire_lease(
            db,
            job=job,
            attempt=attempt,
            now=sweep_now,
        )
    return AttemptExpirationSweepResult(
        inspected=len(stale_jobs),
        requeued=len(stale_jobs),
        repaired_without_attempt=repaired_without_attempt,
    )


async def _load_current_attempt(db: AsyncSession, job: Job) -> JobAttempt | None:
    if not job.lease_token or int(job.attempt or 0) <= 0:
        return None
    result = await db.execute(
        select(JobAttempt).where(
            JobAttempt.tenant_id == job.tenant_id,
            JobAttempt.job_id == job.job_id,
            JobAttempt.attempt_no == job.attempt,
            JobAttempt.lease_token == job.lease_token,
        )
    )
    return result.scalars().first()
