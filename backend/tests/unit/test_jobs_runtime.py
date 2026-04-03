from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.jobs import (
    JobActionRequest,
    JobAttemptResponse,
    JobExplainResponse,
    JobFailRequest,
    JobProgressRequest,
    JobRenewRequest,
    cancel_job,
    explain_job,
    fail_job,
    list_job_attempts,
    renew_job_lease,
    report_job_progress,
    retry_job_now,
)
from backend.core.node_auth import hash_node_token
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _result_first(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _result_all(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


def _rows_result(values: list[tuple[object, object]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = values
    return result


def _noop_result() -> MagicMock:
    return MagicMock()


def _node(*, token_hash: str, enrollment_status: str = "active", **overrides: object) -> Node:
    now = _utcnow()
    node = Node(
        tenant_id="default",
        node_id="node-a",
        name="runner-a",
        node_type="runner",
        address=None,
        profile="go-runner",
        executor="go-native",
        os="darwin",
        arch="arm64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        auth_token_hash=token_hash,
        auth_token_version=1,
        enrollment_status=enrollment_status,
        status="online",
        capabilities=["connector.invoke"],
        metadata_json={"runtime": "go"},
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def _job(**overrides: object) -> Job:
    now = _utcnow()
    job = Job(
        tenant_id="default",
        job_id="job-a",
        kind="connector.invoke",
        status="leased",
        node_id="node-a",
        connector_id=None,
        idempotency_key=None,
        priority=60,
        target_os="darwin",
        target_arch="arm64",
        required_capabilities=["connector.invoke"],
        target_zone="lab-a",
        timeout_seconds=300,
        max_retries=1,
        retry_count=0,
        estimated_duration_s=30,
        source="connectors.invoke",
        created_by="admin",
        payload={"action": "ping"},
        result=None,
        error_message=None,
        lease_seconds=30,
        lease_token="lease-a",
        attempt=1,
        leased_until=now + datetime.timedelta(seconds=15),
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
        tenant_id="default",
        attempt_id="attempt-a",
        job_id="job-a",
        node_id="node-a",
        lease_token="lease-a",
        attempt_no=1,
        status="leased",
        score=88,
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
async def test_fail_job_requeues_when_retry_budget_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    token_hash = hash_node_token("node-token")
    job = _job(max_retries=2, retry_count=0)
    attempt = _attempt()
    db.execute.side_effect = [
        _result_first(_node(token_hash=token_hash)),
        _result_first(job),
        _result_first(attempt),
    ]

    response = await fail_job(
        "job-a",
        JobFailRequest(tenant_id="default", node_id="node-a", lease_token="lease-a", attempt=1, error="boom"),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.status == "pending"
    assert response.retry_count == 1
    assert response.node_id is None
    assert response.lease_state == "none"
    assert response.actions[0].enabled is True
    assert job.lease_token is None
    assert attempt.status == "failed"
    assert attempt.error_message == "boom"
    assert db.flush.await_count >= 1


@pytest.mark.asyncio
async def test_fail_job_marks_terminal_failure_when_budget_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    token_hash = hash_node_token("node-token")
    job = _job(max_retries=0, retry_count=0)
    attempt = _attempt()
    db.execute.side_effect = [
        _result_first(_node(token_hash=token_hash)),
        _result_first(job),
        _result_first(attempt),
    ]

    response = await fail_job(
        "job-a",
        JobFailRequest(tenant_id="default", node_id="node-a", lease_token="lease-a", attempt=1, error="boom"),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.status == "failed"
    assert response.retry_count == 0
    assert response.node_id == "node-a"
    assert response.attention_reason == "terminal failure needs retry or triage"
    assert attempt.status == "failed"


@pytest.mark.asyncio
async def test_list_job_attempts_returns_newest_first() -> None:
    db = AsyncMock()
    db.execute.return_value = _result_all(
        [
            _attempt(attempt_id="attempt-2", attempt_no=2),
            _attempt(attempt_id="attempt-1", attempt_no=1),
        ]
    )

    response = await list_job_attempts("job-a", current_user={"sub": "admin"}, db=db)

    assert [item.attempt_no for item in response] == [2, 1]
    assert all(isinstance(item, JobAttemptResponse) for item in response)


@pytest.mark.asyncio
async def test_report_job_progress_marks_attempt_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    token_hash = hash_node_token("node-token")
    job = _job()
    attempt = _attempt()
    db.execute.side_effect = [
        _result_first(_node(token_hash=token_hash)),
        _result_first(job),
        _result_first(attempt),
    ]

    response = await report_job_progress(
        "job-a",
        JobProgressRequest(
            tenant_id="default",
            node_id="node-a",
            lease_token="lease-a",
            attempt=1,
            progress=25,
            message="working",
        ),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.status == "leased"
    assert attempt.status == "running"
    assert attempt.result_summary == {"progress": 25, "message": "working"}
    assert response.lease_state == "active"


@pytest.mark.asyncio
async def test_renew_job_lease_extends_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    token_hash = hash_node_token("node-token")
    job = _job(leased_until=_utcnow() + datetime.timedelta(seconds=10))
    attempt = _attempt()
    db.execute.side_effect = [
        _result_first(_node(token_hash=token_hash)),
        _result_first(job),
        _result_first(attempt),
    ]

    response = await renew_job_lease(
        "job-a",
        JobRenewRequest(tenant_id="default", node_id="node-a", lease_token="lease-a", attempt=1, extend_seconds=45),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.lease_token == "lease-a"
    assert job.leased_until is not None
    assert attempt.status == "running"
    assert response.actions[2].key == "explain"


@pytest.mark.asyncio
async def test_cancel_job_marks_job_canceled() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    job = _job(status="leased")
    attempt = _attempt()
    db.execute.side_effect = [_result_first(job), _result_first(attempt)]

    response = await cancel_job(
        "job-a",
        JobActionRequest(reason="operator canceled"),
        current_user={"role": "admin"},
        db=db,
        redis=None,
    )

    assert response.status == "canceled"
    assert attempt.status == "canceled"
    assert response.attention_reason == "job canceled by operator"


@pytest.mark.asyncio
async def test_retry_job_now_requeues_terminal_job() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    job = _job(status="failed", node_id="node-a", completed_at=_utcnow(), error_message="boom", retry_count=1, attempt=3)
    db.execute.return_value = _result_first(job)

    response = await retry_job_now(
        "job-a",
        JobActionRequest(reason="manual retry"),
        current_user={"role": "admin"},
        db=db,
        redis=None,
    )

    assert response.status == "pending"
    assert response.retry_count == 0
    assert response.attempt == 0
    assert response.node_id is None
    assert response.actions[0].enabled is True


@pytest.mark.asyncio
async def test_explain_job_reports_node_blockers() -> None:
    now = _utcnow()
    db = AsyncMock()
    job = _job(status="pending", node_id=None)
    eligible_node = _node(token_hash="x", max_concurrency=2)
    blocked_node = _node(
        node_id="node-b",
        token_hash="x",
        os="windows",
        drain_status="draining",
        last_seen_at=now - datetime.timedelta(minutes=2),
    )
    db.execute.side_effect = [
        _result_first(job),
        _result_all([eligible_node, blocked_node]),
        _rows_result([("node-a", 0)]),
        _rows_result([("node-a", "completed"), ("node-b", "failed")]),
        _result_all(["node-b"]),
        _result_all([]),  # leased_rows for anti-affinity
        _result_all([]),  # get_all_scheduling_flags (governance context)
    ]

    response = await explain_job("job-a", current_user={"sub": "admin"}, db=db)

    assert isinstance(response, JobExplainResponse)
    assert response.total_nodes == 2
    assert response.eligible_nodes == 1
    assert response.decisions[0].eligible is True
    assert response.decisions[1].eligible is False
    assert "drain=draining" in response.decisions[1].reasons
