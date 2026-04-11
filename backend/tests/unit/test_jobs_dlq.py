from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.control_plane.adapters.jobs.dlq import list_dead_letter_queue, requeue_job_from_dead_letter
from backend.control_plane.adapters.jobs.models import JobRequeueRequest
from backend.runtime.execution.failure_taxonomy import FailureCategory, should_retry_job


def _result_for_scalars(value: object) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = value
    result.scalars.return_value = scalars
    return result


def _failed_job(**overrides: object) -> SimpleNamespace:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    job = SimpleNamespace(
        tenant_id="tenant-a",
        job_id="job-1",
        kind="shell.exec",
        status="failed",
        retry_count=2,
        max_retries=3,
        priority=50,
        connector_id=None,
        idempotency_key=None,
        target_os=None,
        target_arch=None,
        target_executor=None,
        required_capabilities=[],
        target_zone=None,
        required_cpu_cores=None,
        required_memory_mb=None,
        required_gpu_vram_mb=None,
        required_storage_mb=None,
        timeout_seconds=300,
        estimated_duration_s=None,
        source="console",
        attempt=1,
        payload={},
        result=None,
        lease_seconds=30,
        created_at=now,
        attempt_count=0,
        queue_class="interactive",
        worker_pool="default",
        node_id="node-a",
        lease_token="lease-1",
        leased_until=now,
        started_at=now,
        completed_at=now,
        error_message="boom",
        failure_category="timeout",
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


@pytest.mark.asyncio
async def test_list_dead_letter_queue_bypasses_redis_fast_path_when_filtered() -> None:
    db = AsyncMock()
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    db.execute.side_effect = [
        count_result,
        _result_for_scalars([_failed_job()]),
    ]
    redis = cast(Any, SimpleNamespace(zcard=AsyncMock(return_value=9), zrevrange=AsyncMock(return_value=["job-1"])))

    response = await list_dead_letter_queue(
        limit=20,
        offset=0,
        failure_category="timeout",
        current_user={"tenant_id": "tenant-a", "username": "admin"},
        db=db,
        redis=redis,
    )

    assert response.total == 1
    assert len(response.items) == 1
    redis.zcard.assert_not_awaited()
    redis.zrevrange.assert_not_awaited()


@pytest.mark.asyncio
async def test_requeue_job_commits_before_removing_redis_index() -> None:
    db = AsyncMock()
    call_order: list[str] = []

    async def _flush() -> None:
        call_order.append("flush")

    async def _commit() -> None:
        call_order.append("commit")

    db.flush.side_effect = _flush
    db.commit.side_effect = _commit
    job = _failed_job()

    async def _remove(*_args: object, **_kwargs: object) -> bool:
        call_order.append("remove")
        return True

    with (
        patch("backend.control_plane.adapters.jobs.dlq._get_job_by_id_for_update", new=AsyncMock(return_value=job)),
        patch("backend.control_plane.adapters.jobs.dlq._append_log", new=AsyncMock()),
        patch("backend.control_plane.adapters.jobs.dlq.remove_from_dead_letter_queue", new=AsyncMock(side_effect=_remove)),
        patch("backend.control_plane.adapters.jobs.dlq.publish_control_event", new=AsyncMock()),
        patch(
            "backend.control_plane.adapters.jobs.dlq._to_response",
            return_value=SimpleNamespace(status="pending", model_dump=lambda **_kwargs: {"job_id": "job-1"}),
        ),
    ):
        response = await requeue_job_from_dead_letter(
            "job-1",
            JobRequeueRequest(reason="retry", reset_retry_count=True),
            current_user={"tenant_id": "tenant-a", "username": "admin"},
            db=db,
            redis=cast(Any, SimpleNamespace()),
        )

    assert response.status == "pending"
    assert call_order[-2:] == ["commit", "remove"]
    assert call_order[0] == "flush"


@pytest.mark.asyncio
async def test_requeue_job_from_dead_letter_resets_attempt_and_retry_budget_when_requested() -> None:
    db = AsyncMock()
    job = _failed_job(
        retry_count=3,
        max_retries=3,
        attempt=4,
        attempt_count=4,
        retry_at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        result={"partial": True},
    )

    with (
        patch("backend.control_plane.adapters.jobs.dlq._get_job_by_id_for_update", new=AsyncMock(return_value=job)),
        patch("backend.control_plane.adapters.jobs.dlq._append_log", new=AsyncMock()),
        patch("backend.control_plane.adapters.jobs.dlq.remove_from_dead_letter_queue", new=AsyncMock(return_value=True)),
        patch("backend.control_plane.adapters.jobs.dlq.publish_control_event", new=AsyncMock()),
    ):
        response = await requeue_job_from_dead_letter(
            "job-1",
            JobRequeueRequest(reason="fresh replay", reset_retry_count=True),
            current_user={"tenant_id": "tenant-a", "username": "admin"},
            db=db,
            redis=cast(Any, SimpleNamespace()),
        )

    assert response.status == "pending"
    assert response.retry_count == 0
    assert response.attempt == 0
    assert response.attempt_count == 0
    assert job.retry_at is None
    assert job.result is None
    assert should_retry_job(cast(Any, job), FailureCategory.TIMEOUT) is True


@pytest.mark.asyncio
async def test_requeue_job_from_dead_letter_can_preserve_existing_budget() -> None:
    db = AsyncMock()
    job = _failed_job(
        retry_count=2,
        max_retries=2,
        attempt=3,
        attempt_count=3,
    )

    with (
        patch("backend.control_plane.adapters.jobs.dlq._get_job_by_id_for_update", new=AsyncMock(return_value=job)),
        patch("backend.control_plane.adapters.jobs.dlq._append_log", new=AsyncMock()),
        patch("backend.control_plane.adapters.jobs.dlq.remove_from_dead_letter_queue", new=AsyncMock(return_value=True)),
        patch("backend.control_plane.adapters.jobs.dlq.publish_control_event", new=AsyncMock()),
    ):
        response = await requeue_job_from_dead_letter(
            "job-1",
            JobRequeueRequest(reason="preserve budget", reset_retry_count=False),
            current_user={"tenant_id": "tenant-a", "username": "admin"},
            db=db,
            redis=cast(Any, SimpleNamespace()),
        )

    assert response.status == "pending"
    assert response.retry_count == 2
    assert response.attempt_count == 3
    assert response.attempt == 0
    assert should_retry_job(cast(Any, job), FailureCategory.TIMEOUT) is False
