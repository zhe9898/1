from __future__ import annotations

import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.execution.job_status import canonicalize_job_attempt_status_input, canonicalize_job_status_input
from backend.kernel.execution.lease_service import LeaseService
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt


class JobLifecycleService:
    @staticmethod
    async def _reset_for_fresh_attempt(
        db: AsyncSession,
        *,
        job: Job,
        now: datetime.datetime,
        reset_retry_budget: bool,
    ) -> None:
        """Reset runtime state for a fresh replay or manual retry cycle."""
        await LeaseService.reset_lease_projection(
            db,
            job=job,
            now=now,
            reset_attempt=True,
        )
        job.status = "pending"
        job.node_id = None
        job.retry_at = None
        job.result = None
        job.error_message = None
        job.failure_category = None
        job.started_at = None
        job.completed_at = None
        if reset_retry_budget:
            job.retry_count = 0
            job.attempt_count = 0
        job.updated_at = now
        await db.flush()

    @staticmethod
    async def expire_lease(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt | None,
        now: datetime.datetime,
        error_message: str = "lease expired before completion",
    ) -> None:
        if attempt is None:
            await LeaseService.reset_lease_projection(db, job=job, now=now)
        else:
            await LeaseService.clear_active_lease(
                db,
                job=job,
                attempt=attempt,
                now=now,
                attempt_status=canonicalize_job_attempt_status_input("timeout"),
                attempt_error=error_message,
            )
        job.status = canonicalize_job_status_input("pending")
        job.node_id = None
        job.retry_at = None
        job.result = None
        job.error_message = error_message
        job.failure_category = "lease_timeout"
        job.started_at = None
        job.completed_at = None
        job.updated_at = now
        await db.flush()

    @staticmethod
    async def complete_job(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt | None,
        result: dict[str, object],
        now: datetime.datetime,
    ) -> None:
        await LeaseService.clear_active_lease(
            db,
            job=job,
            attempt=attempt,
            now=now,
            attempt_status="completed",
            attempt_result=result,
        )
        job.status = "completed"
        job.result = result
        job.error_message = None
        job.completed_at = now
        await db.flush()

    @staticmethod
    async def requeue_after_failure(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt | None,
        error_message: str,
        failure_category: str,
        retry_at: datetime.datetime,
        now: datetime.datetime,
    ) -> None:
        await LeaseService.clear_active_lease(
            db,
            job=job,
            attempt=attempt,
            now=now,
            attempt_status="failed",
            attempt_error=error_message,
        )
        job.retry_count = int(job.retry_count or 0) + 1
        job.status = "pending"
        job.node_id = None
        job.error_message = error_message
        job.failure_category = failure_category
        job.completed_at = None
        job.started_at = None
        job.retry_at = retry_at
        await db.flush()

    @staticmethod
    async def fail_job(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt | None,
        error_message: str,
        failure_category: str,
        now: datetime.datetime,
    ) -> None:
        await LeaseService.clear_active_lease(
            db,
            job=job,
            attempt=attempt,
            now=now,
            attempt_status="failed",
            attempt_error=error_message,
        )
        job.status = "failed"
        job.error_message = error_message
        job.failure_category = failure_category
        job.completed_at = now
        await db.flush()

    @staticmethod
    async def cancel_job(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt | None,
        reason: str,
        now: datetime.datetime,
    ) -> None:
        await LeaseService.clear_active_lease(
            db,
            job=job,
            attempt=attempt,
            now=now,
            attempt_status=canonicalize_job_attempt_status_input("cancelled") if attempt is not None else None,
            attempt_error=reason,
        )
        job.status = canonicalize_job_status_input("cancelled")
        job.error_message = reason
        job.completed_at = now
        await db.flush()

    @staticmethod
    async def retry_job(
        db: AsyncSession,
        *,
        job: Job,
        now: datetime.datetime,
    ) -> None:
        await JobLifecycleService._reset_for_fresh_attempt(
            db,
            job=job,
            now=now,
            reset_retry_budget=True,
        )

    @staticmethod
    async def requeue_from_dead_letter(
        db: AsyncSession,
        *,
        job: Job,
        now: datetime.datetime,
        reset_retry_count: bool,
        increase_max_retries: int | None,
    ) -> None:
        await JobLifecycleService._reset_for_fresh_attempt(
            db,
            job=job,
            now=now,
            reset_retry_budget=reset_retry_count,
        )
        if increase_max_retries is not None:
            job.max_retries = job.max_retries + increase_max_retries
        await db.flush()

    @staticmethod
    async def expire_deadline(
        db: AsyncSession,
        *,
        job: Job,
        now: datetime.datetime,
    ) -> None:
        job.status = "failed"
        job.error_message = f"deadline expired at {job.deadline_at.isoformat()}" if job.deadline_at else "deadline expired"
        job.failure_category = "deadline_expired"
        job.updated_at = now
        await db.flush()

    @staticmethod
    async def preempt_job(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt | None,
        reason: str,
        now: datetime.datetime,
    ) -> None:
        await LeaseService.clear_active_lease(
            db,
            job=job,
            attempt=attempt,
            now=now,
            attempt_status=canonicalize_job_attempt_status_input("cancelled") if attempt is not None else None,
            attempt_error=reason,
        )
        job.status = "pending"
        job.node_id = None
        job.started_at = None
        job.completed_at = None
        await db.flush()
