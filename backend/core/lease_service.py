from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import Job
from backend.models.job_attempt import JobAttempt


def _new_lease_token() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    job: Job
    attempt: JobAttempt
    lease_token: str
    attempt_no: int


class LeaseService:
    """Single write entrypoint for lease lifecycle mutations."""

    @staticmethod
    async def expire_previous_attempt_if_needed(
        db: AsyncSession,
        *,
        job: Job,
        now: datetime.datetime,
        attempt: JobAttempt | None,
    ) -> None:
        if attempt is None:
            return
        if attempt.status not in {"leased", "running"}:
            return
        if not job.leased_until or job.leased_until >= now:
            return
        attempt.status = "expired"
        attempt.error_message = "lease expired before completion"
        attempt.completed_at = now
        attempt.updated_at = now
        await db.flush()

    @staticmethod
    async def grant_lease(
        db: AsyncSession,
        *,
        job: Job,
        node_id: str,
        score: int,
        now: datetime.datetime,
        scheduling_decision_id: int | None = None,
    ) -> LeaseGrant:
        lease_token = _new_lease_token()
        attempt_no = int(job.attempt or 0) + 1
        job.status = "leased"
        job.node_id = node_id
        job.attempt = attempt_no
        job.attempt_count = int(getattr(job, "attempt_count", 0) or 0) + 1
        job.lease_token = lease_token
        job.result = None
        job.error_message = None
        job.completed_at = None
        job.started_at = now
        job.leased_until = now + datetime.timedelta(seconds=job.lease_seconds)
        job.updated_at = now
        attempt = JobAttempt(
            tenant_id=job.tenant_id,
            attempt_id=str(uuid.uuid4()),
            job_id=job.job_id,
            node_id=node_id,
            lease_token=lease_token,
            attempt_no=attempt_no,
            scheduling_decision_id=scheduling_decision_id,
            status="leased",
            score=score,
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        db.add(attempt)
        await db.flush()
        return LeaseGrant(job=job, attempt=attempt, lease_token=lease_token, attempt_no=attempt_no)

    @staticmethod
    async def mark_attempt_running(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt,
        now: datetime.datetime,
    ) -> None:
        attempt.status = "running"
        attempt.updated_at = now
        job.started_at = job.started_at or now
        job.updated_at = now
        await db.flush()

    @staticmethod
    async def renew_lease(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt,
        now: datetime.datetime,
        extend_seconds: int,
    ) -> str:
        new_token = _new_lease_token()
        attempt.status = "running"
        attempt.lease_token = new_token
        attempt.updated_at = now
        job.lease_token = new_token
        job.leased_until = now + datetime.timedelta(seconds=extend_seconds)
        job.updated_at = now
        await db.flush()
        return new_token

    @staticmethod
    async def attach_scheduling_decision(
        db: AsyncSession,
        *,
        attempt: JobAttempt,
        scheduling_decision_id: int,
        now: datetime.datetime,
    ) -> None:
        attempt.scheduling_decision_id = scheduling_decision_id
        attempt.updated_at = now
        await db.flush()

    @staticmethod
    async def clear_active_lease(
        db: AsyncSession,
        *,
        job: Job,
        attempt: JobAttempt | None,
        now: datetime.datetime,
        attempt_status: str | None = None,
        attempt_error: str | None = None,
        attempt_result: dict[str, object] | None = None,
    ) -> None:
        if attempt is not None and attempt_status:
            attempt.status = attempt_status
            attempt.error_message = attempt_error
            attempt.result_summary = attempt_result
            attempt.completed_at = now
            attempt.updated_at = now
        job.leased_until = None
        job.lease_token = None
        job.updated_at = now
        await db.flush()

    @staticmethod
    async def reset_lease_projection(
        db: AsyncSession,
        *,
        job: Job,
        now: datetime.datetime,
        reset_attempt: bool = False,
    ) -> None:
        job.lease_token = None
        job.leased_until = None
        if reset_attempt:
            job.attempt = 0
        job.updated_at = now
        await db.flush()
