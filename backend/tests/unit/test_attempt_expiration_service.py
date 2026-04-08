from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.kernel.execution.attempt_expiration_service import expire_stale_attempts
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _scalars_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    scalars.first.return_value = values[0] if values else None
    result.scalars.return_value = scalars
    return result


def _job(**overrides: object) -> Job:
    now = _utcnow()
    job = Job(
        tenant_id="tenant-a",
        job_id="job-1",
        kind="connector.invoke",
        status="leased",
        node_id="node-a",
        connector_id="connector-1",
        idempotency_key=None,
        priority=50,
        queue_class="interactive",
        worker_pool="default",
        target_os=None,
        target_arch=None,
        target_executor=None,
        required_capabilities=[],
        target_zone=None,
        timeout_seconds=300,
        max_retries=0,
        retry_count=0,
        attempt_count=1,
        estimated_duration_s=None,
        source="console",
        created_by="tester",
        payload={},
        result={"stale": True},
        error_message=None,
        lease_seconds=30,
        lease_token="lease-1",
        attempt=1,
        leased_until=now - datetime.timedelta(seconds=5),
        retry_at=None,
        created_at=now,
        started_at=now,
        completed_at=None,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _attempt(**overrides: object) -> JobAttempt:
    now = _utcnow()
    attempt = JobAttempt(
        tenant_id="tenant-a",
        attempt_id="attempt-1",
        job_id="job-1",
        node_id="node-a",
        lease_token="lease-1",
        attempt_no=1,
        scheduling_decision_id=None,
        status="running",
        score=90,
        error_message=None,
        result_summary=None,
        created_at=now,
        started_at=now,
        completed_at=None,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(attempt, key, value)
    return attempt


@pytest.mark.asyncio
async def test_expire_stale_attempts_requeues_leased_job_and_marks_attempt_timeout() -> None:
    stale_job = _job()
    current_attempt = _attempt()
    db = AsyncMock()
    db.flush = AsyncMock()
    db.execute.side_effect = [
        _scalars_result([stale_job]),
        _scalars_result([current_attempt]),
    ]

    result = await expire_stale_attempts(db, now=_utcnow())

    assert result.inspected == 1
    assert result.requeued == 1
    assert result.repaired_without_attempt == 0
    assert stale_job.status == "pending"
    assert stale_job.lease_token is None
    assert stale_job.leased_until is None
    assert stale_job.node_id is None
    assert stale_job.failure_category == "lease_timeout"
    assert current_attempt.status == "timeout"


@pytest.mark.asyncio
async def test_expire_stale_attempts_repairs_projection_when_attempt_row_is_missing() -> None:
    stale_job = _job(job_id="job-2", lease_token="lease-2")
    db = AsyncMock()
    db.flush = AsyncMock()
    db.execute.side_effect = [
        _scalars_result([stale_job]),
        _scalars_result([]),
    ]

    result = await expire_stale_attempts(db, now=_utcnow())

    assert result.repaired_without_attempt == 1
    assert stale_job.status == "pending"
    assert stale_job.lease_token is None
    assert stale_job.leased_until is None
