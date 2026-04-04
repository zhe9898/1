from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.api.jobs import JobCreateRequest, JobPullRequest, JobResultRequest, complete_job, pull_jobs
from backend.api.jobs.submission import check_concurrent_limits, submit_job
from backend.core.node_auth import hash_node_token
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    scalars.all.return_value = [value] if value is not None else []
    result.scalars.return_value = scalars
    result.scalar.return_value = value
    return result


def _all_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


def _row_result(*, global_count: int = 0, tenant_count: int = 0, connector_count: int = 0) -> MagicMock:
    result = MagicMock()
    result.one.return_value = SimpleNamespace(
        global_count=global_count,
        tenant_count=tenant_count,
        connector_count=connector_count,
    )
    return result


def _job(**overrides: object) -> Job:
    now = _utcnow()
    job = Job(
        tenant_id="default",
        job_id="job-1",
        kind="connector.invoke",
        status="pending",
        node_id=None,
        connector_id="connector-1",
        idempotency_key=None,
        priority=50,
        target_os=None,
        target_arch=None,
        required_capabilities=[],
        target_zone=None,
        timeout_seconds=300,
        max_retries=0,
        retry_count=0,
        source="console",
        created_by="tester",
        payload={"hello": "world"},
        result=None,
        error_message=None,
        lease_seconds=30,
        lease_token=None,
        attempt=0,
        leased_until=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


def _node(token_hash: str, **overrides: object) -> Node:
    now = _utcnow()
    node = Node(
        tenant_id="default",
        node_id="node-a",
        name="runner-a",
        node_type="runner",
        address="http://127.0.0.1:9000",
        profile="go-runner",
        executor="go-native",
        os="windows",
        arch="amd64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        auth_token_hash=token_hash,
        auth_token_version=1,
        enrollment_status="active",
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


def _attempt(**overrides: object) -> JobAttempt:
    now = _utcnow()
    attempt = JobAttempt(
        tenant_id="default",
        attempt_id="attempt-1",
        job_id="job-1",
        node_id="node-a",
        lease_token="token-a",
        attempt_no=1,
        status="leased",
        score=80,
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
async def test_check_concurrent_limits_uses_global_function() -> None:
    db = AsyncMock()
    db.add = MagicMock()

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        rendered = str(statement).lower()
        if "zen70_global_leased_jobs_count" in rendered and "tenant_count" in rendered:
            return _row_result()
        return _scalar_result(None)

    db.execute.side_effect = _execute_side_effect

    await check_concurrent_limits(db, "tenant-alpha", "scheduled")

    rendered_calls = [str(call.args[0]).lower() for call in db.execute.await_args_list]
    assert any("zen70_global_leased_jobs_count" in sql for sql in rendered_calls)
    assert len(rendered_calls) == 1


@pytest.mark.asyncio
async def test_check_concurrent_limits_returns_503_when_global_function_unavailable() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = RuntimeError("function missing")

    with pytest.raises(HTTPException) as exc:
        await check_concurrent_limits(db, "tenant-alpha", "scheduled")

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_submit_job_returns_503_when_transaction_recovery_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock(side_effect=RuntimeError("flush failed"))
    db.rollback = AsyncMock(side_effect=RuntimeError("rollback failed"))
    db.execute.return_value = _scalar_result(None)

    monkeypatch.setattr(
        "backend.core.scheduling_resilience.AdmissionController.check_admission",
        AsyncMock(return_value=(True, "", {})),
    )
    monkeypatch.setattr("backend.api.jobs.submission.validate_job_payload", lambda *_: {"ok": True})
    monkeypatch.setattr("backend.api.jobs.submission.resolve_job_queue_contract", lambda **_: ("interactive", "default"))
    monkeypatch.setattr("backend.api.jobs.submission.check_concurrent_limits", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc:
        await submit_job(
            JobCreateRequest(
                kind="connector.invoke",
                payload={"ok": True},
                idempotency_key="idem-1",
            ),
            current_user={"sub": "u1", "tenant_id": "default"},
            db=db,
            redis=None,
        )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_complete_job_commits_before_publishing_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    order: list[str] = []

    leased = _job(status="leased", node_id="node-a", attempt=1, lease_token="token-a")
    attempt = _attempt()
    node = _node(hash_node_token("node-token"), node_id="node-a")

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    db.execute.side_effect = [_scalar_result(node), _scalar_result(leased), _scalar_result(attempt)]

    reservation_mgr = MagicMock()
    reservation_mgr.get_reservation.return_value = None
    reservation_mgr.cancel_reservation.return_value = False
    monkeypatch.setattr("backend.api.jobs.lifecycle.get_reservation_manager", lambda: reservation_mgr)

    fcp = MagicMock()
    fcp.record_success = AsyncMock(return_value=None)
    fcp.get_kind_circuit_state = AsyncMock(return_value="closed")
    fcp.reset_kind_circuit = AsyncMock(return_value=None)
    monkeypatch.setattr("backend.api.jobs.lifecycle.get_failure_control_plane", lambda: fcp)

    async def _publish(*args: object, **kwargs: object) -> None:
        del args, kwargs
        order.append("publish")

    monkeypatch.setattr("backend.api.jobs.lifecycle.publish_control_event", _publish)

    await complete_job(
        "job-1",
        JobResultRequest(
            tenant_id="default",
            node_id="node-a",
            attempt=1,
            lease_token="token-a",
            result={"ok": True},
        ),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert order[:2] == ["commit", "publish"]


@pytest.mark.asyncio
async def test_pull_jobs_commits_then_publishes_then_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    order: list[str] = []
    pending = _job()
    node = _node(hash_node_token("node-token"), node_id="node-a")

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock(side_effect=lambda: order.append("commit"))

    reservation_mgr = MagicMock()
    reservation_mgr.list_reservations.return_value = []
    reservation_mgr.cleanup_expired = MagicMock()
    reservation_mgr.get_reservation.return_value = None
    reservation_mgr.cancel_reservation.return_value = False
    reservation_mgr.create_reservation.return_value = None
    reservation_mgr.config = SimpleNamespace(reservation_min_priority=90)

    audit = MagicMock()
    audit.context = {}
    governance = MagicMock()
    governance.pre_dispatch_admission = AsyncMock(return_value=SimpleNamespace(admitted=True))
    governance.is_feature_enabled = AsyncMock(return_value=False)
    governance.create_decision_logger.return_value = audit
    governance.should_skip_backoff.return_value = False
    governance.record_backoff_skip_metric = MagicMock()
    governance.filter_by_executor_contract.return_value = SimpleNamespace(compatible=True, reason=None)
    governance.configure_zone_context = MagicMock()
    governance.can_preempt.return_value = (False, None)
    governance.record_preemption_budget_hit = MagicMock()
    governance.record_preemption = MagicMock()
    governance.record_backoff_failure = MagicMock()
    governance.record_backoff_success = MagicMock()
    governance.record_placement_metric = MagicMock()
    governance.record_rejection_metric = MagicMock()
    governance.post_dispatch_audit = AsyncMock(return_value=None)

    fcp = MagicMock()
    fcp.is_in_burst = AsyncMock(return_value=False)
    fcp.get_kind_circuit_state = AsyncMock(return_value="closed")

    selected = SimpleNamespace(job=pending, score=80, eligible_nodes_count=1, score_breakdown={})

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        sql = str(statement).lower()
        if "from jobs" not in sql:
            return _all_result([])
        if "order by" in sql:
            return _all_result([pending])
        return _all_result([])

    db.execute.side_effect = _execute_side_effect

    redis = MagicMock()
    redis.acquire_lock = AsyncMock(return_value=True)
    redis.release_lock = AsyncMock(side_effect=lambda _: order.append("release"))

    async def _publish(*args: object, **kwargs: object) -> None:
        del args, kwargs
        order.append("publish")

    monkeypatch.setattr("backend.api.jobs.dispatch.publish_control_event", _publish)
    monkeypatch.setattr("backend.api.jobs.dispatch.authenticate_node_request", AsyncMock(return_value=node))
    monkeypatch.setattr("backend.api.jobs.dispatch.get_reservation_manager", lambda: reservation_mgr)
    monkeypatch.setattr("backend.api.jobs.dispatch.get_governance_facade", lambda: governance)
    monkeypatch.setattr("backend.api.jobs.dispatch.get_failure_control_plane", lambda: fcp)
    monkeypatch.setattr(
        "backend.api.jobs.dispatch._load_node_metrics",
        AsyncMock(return_value=([node], {"node-a": 0}, {"node-a": 1.0})),
    )
    monkeypatch.setattr("backend.api.jobs.dispatch._build_snapshots", lambda *a, **k: [])
    monkeypatch.setattr("backend.api.jobs.dispatch._append_log", AsyncMock(return_value=None))
    monkeypatch.setattr("backend.api.jobs.dispatch._load_recent_failed_job_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr("backend.api.jobs.dispatch.build_time_budgeted_placement_plan", lambda *a, **k: {})
    monkeypatch.setattr("backend.api.jobs.dispatch.select_jobs_for_node", lambda *a, **k: [selected])
    monkeypatch.setattr("backend.core.queue_stratification.sort_jobs_by_stratified_priority", lambda jobs, **_: jobs)
    monkeypatch.setattr("backend.core.business_scheduling.apply_business_filters", lambda jobs, **_: jobs)

    leased = await pull_jobs(
        JobPullRequest(tenant_id="default", node_id="node-a", limit=1, accepted_kinds=["connector.invoke"]),
        db=db,
        redis=redis,
        node_token="node-token",
    )

    assert len(leased) == 1
    assert order.index("commit") < order.index("publish") < order.index("release")


@pytest.mark.asyncio
async def test_pull_jobs_releases_lock_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    pending = _job()
    node = _node(hash_node_token("node-token"), node_id="node-a")

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock(side_effect=RuntimeError("flush failed"))
    db.commit = AsyncMock()

    reservation_mgr = MagicMock()
    reservation_mgr.list_reservations.return_value = []
    reservation_mgr.cleanup_expired = MagicMock()
    reservation_mgr.get_reservation.return_value = None
    reservation_mgr.cancel_reservation.return_value = False
    reservation_mgr.create_reservation.return_value = None
    reservation_mgr.config = SimpleNamespace(reservation_min_priority=90)

    audit = MagicMock()
    audit.context = {}
    governance = MagicMock()
    governance.pre_dispatch_admission = AsyncMock(return_value=SimpleNamespace(admitted=True))
    governance.is_feature_enabled = AsyncMock(return_value=False)
    governance.create_decision_logger.return_value = audit
    governance.should_skip_backoff.return_value = False
    governance.record_backoff_skip_metric = MagicMock()
    governance.filter_by_executor_contract.return_value = SimpleNamespace(compatible=True, reason=None)
    governance.configure_zone_context = MagicMock()
    governance.can_preempt.return_value = (False, None)
    governance.record_preemption_budget_hit = MagicMock()
    governance.record_preemption = MagicMock()
    governance.record_backoff_failure = MagicMock()
    governance.record_backoff_success = MagicMock()
    governance.record_placement_metric = MagicMock()
    governance.record_rejection_metric = MagicMock()
    governance.post_dispatch_audit = AsyncMock(return_value=None)

    fcp = MagicMock()
    fcp.is_in_burst = AsyncMock(return_value=False)
    fcp.get_kind_circuit_state = AsyncMock(return_value="closed")

    selected = SimpleNamespace(job=pending, score=80, eligible_nodes_count=1, score_breakdown={})

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        sql = str(statement).lower()
        if "from jobs" not in sql:
            return _all_result([])
        if "order by" in sql:
            return _all_result([pending])
        return _all_result([])

    db.execute.side_effect = _execute_side_effect

    redis = MagicMock()
    redis.acquire_lock = AsyncMock(return_value=True)
    redis.release_lock = AsyncMock(return_value=True)

    monkeypatch.setattr("backend.api.jobs.dispatch.authenticate_node_request", AsyncMock(return_value=node))
    monkeypatch.setattr("backend.api.jobs.dispatch.get_reservation_manager", lambda: reservation_mgr)
    monkeypatch.setattr("backend.api.jobs.dispatch.get_governance_facade", lambda: governance)
    monkeypatch.setattr("backend.api.jobs.dispatch.get_failure_control_plane", lambda: fcp)
    monkeypatch.setattr(
        "backend.api.jobs.dispatch._load_node_metrics",
        AsyncMock(return_value=([node], {"node-a": 0}, {"node-a": 1.0})),
    )
    monkeypatch.setattr("backend.api.jobs.dispatch._build_snapshots", lambda *a, **k: [])
    monkeypatch.setattr("backend.api.jobs.dispatch._append_log", AsyncMock(return_value=None))
    monkeypatch.setattr("backend.api.jobs.dispatch._load_recent_failed_job_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr("backend.api.jobs.dispatch.build_time_budgeted_placement_plan", lambda *a, **k: {})
    monkeypatch.setattr("backend.api.jobs.dispatch.select_jobs_for_node", lambda *a, **k: [selected])
    monkeypatch.setattr("backend.core.queue_stratification.sort_jobs_by_stratified_priority", lambda jobs, **_: jobs)
    monkeypatch.setattr("backend.core.business_scheduling.apply_business_filters", lambda jobs, **_: jobs)

    with pytest.raises(RuntimeError, match="flush failed"):
        await pull_jobs(
            JobPullRequest(tenant_id="default", node_id="node-a", limit=1, accepted_kinds=["connector.invoke"]),
            db=db,
            redis=redis,
            node_token="node-token",
        )

    redis.release_lock.assert_awaited_once()
