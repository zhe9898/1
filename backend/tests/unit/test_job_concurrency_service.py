from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.kernel.execution.job_concurrency_service import build_job_concurrency_window
from backend.models.job import Job


def _row_result(*, global_count: int, tenant_count: int) -> MagicMock:
    result = MagicMock()
    result.one.return_value = SimpleNamespace(global_count=global_count, tenant_count=tenant_count)
    return result


def _rows_result(rows: list[tuple[object, object]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _job(**overrides: object) -> Job:
    job = Job(
        tenant_id="tenant-a",
        job_id="job-1",
        kind="connector.invoke",
        status="pending",
        node_id=None,
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
        attempt_count=0,
        estimated_duration_s=None,
        source="console",
        created_by="tester",
        payload={},
        result=None,
        error_message=None,
        lease_seconds=30,
        lease_token=None,
        attempt=0,
        leased_until=None,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


@pytest.mark.asyncio
async def test_assert_capacity_uses_global_function_and_connector_scope() -> None:
    db = AsyncMock()

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        rendered = str(statement).lower()
        if "pg_advisory_xact_lock" in rendered:
            return MagicMock()
        if "zen70_global_leased_jobs_count" in rendered:
            return _row_result(global_count=0, tenant_count=0)
        return _rows_result([("connector-1", 0)])

    db.execute.side_effect = _execute_side_effect

    window = build_job_concurrency_window(db=db, tenant_id="tenant-a")
    await window.assert_capacity(job_type="background", connector_id="connector-1")

    rendered = [str(call.args[0]).lower() for call in db.execute.await_args_list]
    assert any("zen70_global_leased_jobs_count" in sql for sql in rendered)
    assert any("group by jobs.connector_id" in sql or "group by job.connector_id" in sql for sql in rendered)


@pytest.mark.asyncio
async def test_assert_capacity_raises_http_429_for_connector_limit() -> None:
    db = AsyncMock()

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        rendered = str(statement).lower()
        if "pg_advisory_xact_lock" in rendered:
            return MagicMock()
        if "zen70_global_leased_jobs_count" in rendered:
            return _row_result(global_count=0, tenant_count=0)
        return _rows_result([("connector-1", 20)])

    db.execute.side_effect = _execute_side_effect

    window = build_job_concurrency_window(db=db, tenant_id="tenant-a")
    with pytest.raises(HTTPException) as exc:
        await window.assert_capacity(job_type="background", connector_id="connector-1")

    assert exc.value.status_code == 429
    assert "connector" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_note_lease_granted_consumes_cached_capacity_without_requery() -> None:
    db = AsyncMock()

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        rendered = str(statement).lower()
        if "pg_advisory_xact_lock" in rendered:
            return MagicMock()
        if "zen70_global_leased_jobs_count" in rendered:
            return _row_result(global_count=0, tenant_count=0)
        return _rows_result([("connector-1", 0)])

    db.execute.side_effect = _execute_side_effect

    window = build_job_concurrency_window(db=db, tenant_id="tenant-a")
    await window.assert_capacity_for_job(_job())
    window.note_lease_granted(_job())
    violation = await window.check_capacity_for_job(_job(connector_id="connector-1"))

    assert violation is None
    assert len(db.execute.await_args_list) == 5
