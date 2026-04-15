from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend.control_plane.adapters.deps import get_node_machine_token
from backend.control_plane.adapters.jobs import (
    JobCreateRequest,
    JobFailRequest,
    JobPullRequest,
    JobResultRequest,
    complete_job,
    create_job,
    fail_job,
    pull_jobs,
)
from backend.control_plane.adapters.nodes import (
    NodeDrainRequest,
    NodeHeartbeatRequest,
    NodeProvisionRequest,
    NodeRegisterRequest,
    drain_node,
    heartbeat_node,
    provision_node,
    register_node,
    revoke_node,
    rotate_node_token,
)
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.node import Node
from backend.runtime.topology.node_auth import hash_node_token


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _all_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    scalars.first.return_value = values[0] if values else None
    result.scalars.return_value = scalars
    return result


def _rows_result(values: list[tuple[object, object]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = values
    return result


class _FakeConcurrencyWindow:
    async def assert_capacity(self, *, job_type: str, connector_id: str | None = None) -> None:
        del job_type, connector_id

    async def check_capacity_for_job(self, job: Job) -> None:
        del job
        return None

    def note_lease_granted(self, job: Job) -> None:
        del job


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
        estimated_duration_s=None,
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


def _node(**overrides: object) -> Node:
    now = _utcnow()
    node = Node(
        tenant_id="default",
        node_id="node-1",
        name="runner-1",
        node_type="runner",
        address="http://127.0.0.1:9000",
        profile="go-runner",
        executor="go-native",
        os="windows",
        arch="amd64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        auth_token_hash="$2b$04$placeholderplaceholderplaceholderplaceholderplaceho",
        auth_token_version=1,
        enrollment_status="approved",
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


def _hash_token(monkeypatch: pytest.MonkeyPatch, token: str) -> str:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    return hash_node_token(token)


@pytest.mark.asyncio
async def test_create_job_reuses_existing_idempotency_key() -> None:
    existing = _job(
        job_id="job-existing",
        idempotency_key="invoke-1",
        connector_id="connector-1",
        payload={"connector_id": "connector-1", "action": "read", "parameters": {"hello": "world"}},
    )
    db = AsyncMock()
    db.add = MagicMock()
    admission_result = MagicMock()
    admission_result.scalar.return_value = 0
    db.execute.side_effect = [admission_result, _scalar_result(existing)]

    response = await create_job(
        JobCreateRequest(
            kind="connector.invoke",
            connector_id="connector-1",
            payload={"connector_id": "connector-1", "action": "read", "parameters": {"hello": "world"}},
            lease_seconds=30,
            idempotency_key="invoke-1",
        ),
        current_user={"sub": "tester", "tenant_id": "default", "role": "admin"},
        db=db,
        redis=None,
    )

    assert response.job_id == "job-existing"
    assert response.idempotency_key == "invoke-1"
    assert response.priority == 50
    assert response.queue_class == "interactive"
    assert response.worker_pool == "interactive"
    assert response.status_view.key == "pending"
    assert response.lease_state_view.key == "none"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_job_rejects_conflicting_idempotency_key() -> None:
    existing = _job(
        job_id="job-existing",
        idempotency_key="invoke-1",
        connector_id="connector-1",
        payload={"connector_id": "connector-1", "action": "read", "parameters": {"hello": "other"}},
    )
    db = AsyncMock()
    db.add = MagicMock()
    admission_result = MagicMock()
    admission_result.scalar.return_value = 0
    db.execute.side_effect = [admission_result, _scalar_result(existing)]

    with pytest.raises(HTTPException) as exc:
        await create_job(
            JobCreateRequest(
                kind="connector.invoke",
                connector_id="connector-1",
                payload={"connector_id": "connector-1", "action": "read", "parameters": {"hello": "world"}},
                lease_seconds=30,
                idempotency_key="invoke-1",
            ),
            current_user={"sub": "tester", "tenant_id": "default", "role": "admin"},
            db=db,
            redis=None,
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_pull_jobs_assigns_attempt_and_lease_token(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _job()
    node = _node(node_id="node-a", auth_token_hash=_hash_token(monkeypatch, "node-token"))
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    reservation_mgr = MagicMock()
    reservation_mgr.list_reservations.return_value = []
    reservation_mgr.cleanup_expired = MagicMock()
    reservation_mgr.get_reservation.return_value = None
    reservation_mgr.cancel_reservation.return_value = False
    reservation_mgr.create_reservation.return_value = None
    reservation_mgr.config = SimpleNamespace(reservation_min_priority=90)

    audit = MagicMock()
    audit.context = {}
    audit.candidates_count = 0

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

    selected = SimpleNamespace(
        job=pending,
        score=80,
        eligible_nodes_count=1,
        score_breakdown={},
    )

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        sql = str(statement).lower()
        if "from jobs" not in sql:
            return _all_result([])
        if "order by" in sql:
            return _all_result([pending])  # candidate query
        return _all_result([])  # leased snapshot and dlq scan

    db.execute.side_effect = _execute_side_effect

    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.authenticate_node_request", AsyncMock(return_value=node))
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.get_reservation_manager", lambda: reservation_mgr)
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.get_governance_facade", lambda: governance)
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.get_failure_control_plane", lambda: fcp)
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._load_node_metrics",
        AsyncMock(return_value=([node], {"node-a": 0}, {"node-a": 1.0})),
    )
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._build_snapshots", lambda *a, **k: [])
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._append_log", AsyncMock(return_value=None))
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._load_recent_failed_job_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.select_jobs_for_node", lambda *a, **k: [selected])
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.build_job_concurrency_window", lambda **_: _FakeConcurrencyWindow())
    monkeypatch.setattr("backend.runtime.scheduling.queue_stratification.sort_jobs_by_stratified_priority", lambda jobs, **_: jobs)
    monkeypatch.setattr("backend.runtime.scheduling.business_scheduling.apply_business_filters", lambda jobs, **_: jobs)

    leased = await pull_jobs(
        JobPullRequest(tenant_id="default", node_id="node-a", limit=1, accepted_kinds=["connector.invoke"]),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert len(leased) == 1
    assert leased[0].node_id == "node-a"
    assert leased[0].attempt == 1
    assert leased[0].lease_token
    assert leased[0].status_view.key == "running"
    assert leased[0].lease_state_view.key == "active"
    assert pending.status == "leased"
    assert pending.attempt == 1
    assert pending.lease_token == leased[0].lease_token


@pytest.mark.asyncio
async def test_pull_jobs_attaches_scheduling_decision_and_policy_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = _job()
    node = _node(node_id="node-a", auth_token_hash=_hash_token(monkeypatch, "node-token"))
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    reservation_mgr = MagicMock()
    reservation_mgr.list_reservations.return_value = []
    reservation_mgr.cleanup_expired = MagicMock()
    reservation_mgr.get_reservation.return_value = None
    reservation_mgr.cancel_reservation.return_value = False
    reservation_mgr.create_reservation.return_value = None
    reservation_mgr.config = SimpleNamespace(reservation_min_priority=90)

    audit = MagicMock()
    audit.context = {}
    audit.candidates_count = 0

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
    governance.post_dispatch_audit = AsyncMock(return_value=SimpleNamespace(id=17))

    fcp = MagicMock()
    fcp.is_in_burst = AsyncMock(return_value=False)
    fcp.get_kind_circuit_state = AsyncMock(return_value="closed")

    selected = SimpleNamespace(
        job=pending,
        score=90,
        eligible_nodes_count=1,
        score_breakdown={"base": 90},
    )

    def _execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        sql = str(statement).lower()
        if "from jobs" not in sql:
            return _all_result([])
        if "order by" in sql:
            return _all_result([pending])
        return _all_result([])

    db.execute.side_effect = _execute_side_effect

    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.authenticate_node_request", AsyncMock(return_value=node))
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.get_reservation_manager", lambda: reservation_mgr)
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.get_governance_facade", lambda: governance)
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.get_failure_control_plane", lambda: fcp)
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._load_node_metrics",
        AsyncMock(return_value=([node], {"node-a": 0}, {"node-a": 1.0})),
    )
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._build_snapshots", lambda *a, **k: [])
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._append_log", AsyncMock(return_value=None))
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._load_recent_failed_job_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.select_jobs_for_node", lambda *a, **k: [selected])
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service.build_job_concurrency_window", lambda **_: _FakeConcurrencyWindow())
    monkeypatch.setattr("backend.runtime.scheduling.queue_stratification.sort_jobs_by_stratified_priority", lambda jobs, **_: jobs)
    monkeypatch.setattr("backend.runtime.scheduling.business_scheduling.apply_business_filters", lambda jobs, **_: jobs)

    await pull_jobs(
        JobPullRequest(tenant_id="default", node_id="node-a", limit=1, accepted_kinds=["connector.invoke"]),
        db=db,
        redis=None,
        node_token="node-token",
    )

    attempts = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], JobAttempt)]
    assert len(attempts) == 1
    assert attempts[0].scheduling_decision_id == 17
    assert audit.context["policy_snapshot"]["policy_version"] >= 0
    assert "quota_version" in audit.context["policy_snapshot"]
    assert "governance_version" in audit.context["policy_snapshot"]


@pytest.mark.asyncio
async def test_complete_job_rejects_stale_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    leased = _job(status="leased", node_id="node-a", attempt=2, lease_token="token-a")
    node = _node(node_id="node-b", auth_token_hash=_hash_token(monkeypatch, "node-token"))
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [_scalar_result(node), _scalar_result(leased)]

    with pytest.raises(HTTPException) as exc:
        await complete_job(
            "job-1",
            JobResultRequest(
                tenant_id="default",
                node_id="node-b",
                attempt=1,
                lease_token="stale-token",
                result={"ok": True},
            ),
            db=db,
            redis=None,
            node_token="node-token",
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_complete_job_is_idempotent_for_same_terminal_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = _job(
        status="completed",
        node_id="node-a",
        attempt=2,
        lease_token="token-a",
        result={"summary": "done"},
        completed_at=_utcnow(),
    )
    node = _node(node_id="node-a", auth_token_hash=_hash_token(monkeypatch, "node-token"))
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [_scalar_result(node), _scalar_result(completed)]

    response = await complete_job(
        "job-1",
        JobResultRequest(
            tenant_id="default",
            node_id="node-a",
            attempt=2,
            lease_token="token-a",
            result={"summary": "done"},
        ),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.status == "completed"
    assert response.result == {"summary": "done"}
    assert response.status_view.key == "completed"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_job_updates_current_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    leased = _job(status="leased", node_id="node-a", attempt=1, lease_token="token-a", max_retries=0)
    attempt = _attempt()
    node = _node(node_id="node-a", auth_token_hash=_hash_token(monkeypatch, "node-token"))
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [_scalar_result(node), _scalar_result(leased), _scalar_result(attempt)]
    db.flush = AsyncMock()

    fcp = MagicMock()
    fcp.record_failure = AsyncMock(return_value=None)
    monkeypatch.setattr("backend.control_plane.adapters.jobs.lifecycle_service.get_failure_control_plane", lambda: fcp)
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.lifecycle_service.infer_failure_category",
        MagicMock(return_value=SimpleNamespace(value="permanent")),
    )
    monkeypatch.setattr("backend.control_plane.adapters.jobs.lifecycle_service.should_retry_job", MagicMock(return_value=False))

    response = await fail_job(
        "job-1",
        JobFailRequest(
            tenant_id="default",
            node_id="node-a",
            attempt=1,
            lease_token="token-a",
            error="boom",
        ),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.status == "failed"
    assert response.error_message is None
    assert response.safe_error_code == "ZEN-JOB-PERMANENT"
    assert response.safe_error_hint == "The job failed with a non-retryable error. Review audit or runner logs before retrying."
    assert response.status_view.key == "failed"
    assert leased.leased_until is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_node_persists_strong_contract_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    node = _node(
        node_id="node-a",
        name="runner-a",
        status="offline",
        enrollment_status="pending",
        auth_token_hash=_hash_token(monkeypatch, "node-token"),
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _scalar_result(node)
    db.flush = AsyncMock()

    response = await register_node(
        NodeRegisterRequest(
            tenant_id="default",
            node_id="node-a",
            name="runner-a",
            node_type="runner",
            address="http://127.0.0.1:9000",
            profile="go-runner",
            executor="go-native",
            os="darwin",
            arch="arm64",
            zone="lab-mac",
            protocol_version="runner.v1",
            lease_version="job-lease.v1",
            capabilities=["connector.invoke"],
            metadata={"runtime": "go"},
        ),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.executor == "go-native"
    assert response.executor_contract == "edge-native"
    assert response.os == "darwin"
    assert response.arch == "arm64"
    assert response.protocol_version == "runner.v1"
    assert response.lease_version == "job-lease.v1"
    # ADR-0047 WP-P0: fresh registration must leave node in 'pending' awaiting admin approval.
    # 'approved' can only be reached via POST /api/v1/nodes/{node_id}/approve — never via register or heartbeat.
    assert response.enrollment_status == "pending"
    assert response.heartbeat_state == "fresh"
    assert response.capacity_state == "available"
    assert response.status_view.key == "online"
    assert response.drain_status_view.key == "active"
    assert [action.key for action in response.actions] == ["rotate_token", "revoke", "drain", "undrain"]


@pytest.mark.asyncio
async def test_heartbeat_updates_existing_node_contract_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _node(auth_token_hash=_hash_token(monkeypatch, "node-token"), enrollment_status="approved")
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [_scalar_result(existing), _rows_result([])]
    db.flush = AsyncMock()

    response = await heartbeat_node(
        NodeHeartbeatRequest(
            tenant_id="default",
            node_id="node-1",
            name="runner-1",
            node_type="runner",
            address="http://10.0.0.8:9000",
            profile="go-runner",
            executor="python-runner",
            os="linux",
            arch="arm64",
            zone="edge-a",
            protocol_version="runner.v2",
            lease_version="job-lease.v2",
            status="online",
            capabilities=["connector.invoke", "noop"],
            metadata={"runtime": "python"},
        ),
        db=db,
        redis=None,
        node_token="node-token",
    )

    assert response.executor == "python-runner"
    assert response.os == "linux"
    assert response.arch == "arm64"
    assert response.zone == "edge-a"
    assert response.worker_pools == ["batch", "interactive"]
    assert response.protocol_version == "runner.v2"
    assert response.lease_version == "job-lease.v2"
    assert response.enrollment_status == "approved"
    assert response.heartbeat_state == "fresh"
    assert response.capacity_state == "available"
    assert response.heartbeat_state_view.key == "fresh"
    assert response.capacity_state_view.key == "available"


@pytest.mark.asyncio
async def test_drain_node_sets_drain_status() -> None:
    existing = _node(node_id="node-drain")
    db = AsyncMock()
    db.execute.return_value = _scalar_result(existing)
    db.flush = AsyncMock()

    response = await drain_node(
        "node-drain",
        NodeDrainRequest(reason="maintenance"),
        current_user={"role": "admin", "tenant_id": "default"},
        db=db,
        redis=None,
    )

    assert response.drain_status == "draining"
    assert response.drain_status_view.key == "draining"
    assert response.health_reason == "maintenance"
    assert "undrain" in {action.key for action in response.actions}


@pytest.mark.asyncio
async def test_provision_node_issues_one_time_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    monkeypatch.delenv("NODE_BOOTSTRAP_GATEWAY_BASE_URL", raising=False)
    db = AsyncMock()
    db.add = MagicMock()
    # Bypass quota check — quota enforcement has its own unit tests.
    # This test focuses on the provisioning response contract.
    monkeypatch.setattr("backend.control_plane.adapters.nodes.check_node_quota", AsyncMock(return_value=None))
    # Node duplicate check → None (no existing node)
    db.execute.return_value = _scalar_result(None)
    db.flush = AsyncMock()

    response = await provision_node(
        NodeProvisionRequest(
            node_id="node-new",
            name="runner-new",
            profile="go-runner",
            capabilities=["connector.invoke"],
            metadata={"runtime": "go"},
        ),
        current_user={"role": "admin", "tenant_id": "tenant-alpha"},
        db=db,
    )

    assert response.node.node_id == "node-new"
    assert response.node.enrollment_status == "pending"
    assert response.node.status == "offline"
    assert response.node.worker_pools == ["interactive"]
    assert response.node.status_view.key == "offline"
    assert response.node.enrollment_status_view.key == "pending"
    assert response.node_token == "<paste-one-time-node-token-here>"
    assert response.auth_token_version == 1
    assert response.bootstrap_commands["powershell"].startswith('$env:RUNNER_NODE_ID="node-new"')
    assert 'export RUNNER_TENANT_ID="tenant-alpha"' in response.bootstrap_commands["unix"]
    assert 'export GATEWAY_BASE_URL="<gateway-base-url>"' in response.bootstrap_commands["unix"]
    assert response.bootstrap_notes
    assert any("HTTPS" in note for note in response.bootstrap_notes)


@pytest.mark.asyncio
async def test_rotate_node_token_increments_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    monkeypatch.setenv("NODE_BOOTSTRAP_GATEWAY_BASE_URL", "https://gateway.example.invalid")
    existing = _node(node_id="node-rotate", auth_token_version=2, enrollment_status="rejected", status="offline")
    db = AsyncMock()
    db.execute.return_value = _scalar_result(existing)
    db.flush = AsyncMock()

    response = await rotate_node_token("node-rotate", current_user={"role": "admin", "tenant_id": "default"}, db=db)

    assert response.node.node_id == "node-rotate"
    assert response.auth_token_version == 3
    assert response.node.enrollment_status == "pending"
    assert response.node.status == "offline"
    assert response.node.status_view.key == "offline"
    assert response.node_token == "<paste-one-time-node-token-here>"
    assert '$env:GATEWAY_BASE_URL="https://gateway.example.invalid"' in response.bootstrap_commands["powershell"].splitlines()


@pytest.mark.asyncio
async def test_provision_node_emits_local_http_opt_in_for_loopback_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_TOKEN_BCRYPT_ROUNDS", "4")
    monkeypatch.setenv("NODE_BOOTSTRAP_GATEWAY_BASE_URL", "http://127.0.0.1:8000")
    db = AsyncMock()
    db.add = MagicMock()
    # Same isolation pattern as test_provision_node_issues_one_time_token
    monkeypatch.setattr("backend.control_plane.adapters.nodes.check_node_quota", AsyncMock(return_value=None))
    db.execute.return_value = _scalar_result(None)
    db.flush = AsyncMock()

    response = await provision_node(
        NodeProvisionRequest(
            node_id="node-local-http",
            name="runner-local-http",
            profile="go-runner",
            capabilities=["connector.invoke"],
            metadata={"runtime": "go"},
        ),
        current_user={"role": "admin", "tenant_id": "tenant-alpha"},
        db=db,
    )

    assert 'RUNNER_ALLOW_INSECURE_HTTP="true"' in response.bootstrap_commands["powershell"]
    assert 'export RUNNER_ALLOW_INSECURE_HTTP="true"' in response.bootstrap_commands["unix"]


@pytest.mark.asyncio
async def test_revoke_node_clears_machine_credentials() -> None:
    existing = _node(node_id="node-revoke", enrollment_status="approved", status="online")
    db = AsyncMock()
    db.execute.return_value = _scalar_result(existing)
    db.flush = AsyncMock()

    response = await revoke_node("node-revoke", current_user={"role": "admin", "tenant_id": "default"}, db=db)

    assert response.node_id == "node-revoke"
    assert response.enrollment_status == "rejected"
    assert response.status == "offline"
    assert response.enrollment_status_view.key == "rejected"
    assert response.status_view.key == "offline"
    assert existing.auth_token_hash is None


@pytest.mark.asyncio
async def test_get_node_machine_token_requires_bearer_header() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_node_machine_token(credentials=None)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_node_machine_token_returns_credentials() -> None:
    token = await get_node_machine_token(
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="node-secret"),
    )

    assert token == "node-secret"
