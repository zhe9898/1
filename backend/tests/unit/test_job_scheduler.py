from __future__ import annotations

import datetime

from backend.core.backfill_scheduling import get_reservation_manager, reset_reservation_manager
from backend.core.job_scheduler import build_node_snapshot, build_time_budgeted_placement_plan, select_jobs_for_node
from backend.models.job import Job
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _job(**overrides: object) -> Job:
    now = _utcnow()
    job = Job(
        tenant_id="default",
        job_id="job-1",
        kind="connector.invoke",
        status="pending",
        node_id=None,
        connector_id=None,
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
        address=None,
        profile="go-runner",
        executor="go-native",
        os="darwin",
        arch="arm64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        auth_token_hash=None,
        auth_token_version=1,
        enrollment_status="active",
        status="online",
        capabilities=["connector.invoke", "shell.exec"],
        metadata_json={},
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def setup_function() -> None:
    reset_reservation_manager()


def teardown_function() -> None:
    reset_reservation_manager()


def test_scheduler_prefers_high_priority_eligible_jobs() -> None:
    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a"), active_lease_count=0, reliability_score=1.0)
    active_nodes = [node]

    low = _job(job_id="job-low", priority=10, created_at=now - datetime.timedelta(minutes=1))
    high = _job(job_id="job-high", priority=90, created_at=now - datetime.timedelta(minutes=1))

    selected = select_jobs_for_node(
        [low, high],
        node,
        active_nodes,
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert len(selected) == 1
    assert selected[0].job.job_id == "job-high"


def test_scheduler_filters_out_platform_mismatch() -> None:
    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a", os="darwin"), active_lease_count=0, reliability_score=1.0)
    active_nodes = [node]
    windows_job = _job(job_id="job-win", target_os="windows")

    selected = select_jobs_for_node(
        [windows_job],
        node,
        active_nodes,
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert selected == []


def test_scheduler_penalizes_same_node_recent_failures() -> None:
    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a"), active_lease_count=0, reliability_score=1.0)
    active_nodes = [node]
    retry_job = _job(job_id="job-retry", priority=80)
    fresh_job = _job(job_id="job-fresh", priority=70)

    selected = select_jobs_for_node(
        [retry_job, fresh_job],
        node,
        active_nodes,
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids={"job-retry"},
        limit=1,
    )

    assert len(selected) == 1
    assert selected[0].job.job_id == "job-fresh"


def test_scheduler_prefers_scarce_job_for_specialized_node() -> None:
    now = _utcnow()
    special_node = build_node_snapshot(
        _node(node_id="node-special", capabilities=["connector.invoke", "vision.gpu"]),
        active_lease_count=0,
        reliability_score=1.0,
    )
    general_node = build_node_snapshot(
        _node(node_id="node-general", capabilities=["connector.invoke"]),
        active_lease_count=0,
        reliability_score=1.0,
    )
    gpu_job = _job(job_id="job-gpu", priority=70, required_capabilities=["vision.gpu"])
    generic_job = _job(job_id="job-generic", priority=70)

    selected = select_jobs_for_node(
        [gpu_job, generic_job],
        special_node,
        [special_node, general_node],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert len(selected) == 1
    assert selected[0].job.job_id == "job-gpu"


def test_scheduler_skips_draining_nodes() -> None:
    now = _utcnow()
    draining = build_node_snapshot(
        _node(node_id="node-drain", drain_status="draining"),
        active_lease_count=0,
        reliability_score=1.0,
    )
    job = _job(job_id="job-drain")

    selected = select_jobs_for_node(
        [job],
        draining,
        [draining],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert selected == []


def test_scheduler_skips_nodes_at_capacity() -> None:
    now = _utcnow()
    saturated = build_node_snapshot(
        _node(node_id="node-full", max_concurrency=1),
        active_lease_count=1,
        reliability_score=1.0,
    )
    job = _job(job_id="job-capacity")

    selected = select_jobs_for_node(
        [job],
        saturated,
        [saturated],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert selected == []


def test_scheduler_limits_selection_to_available_slots() -> None:
    now = _utcnow()
    node = build_node_snapshot(
        _node(node_id="node-cap", max_concurrency=2),
        active_lease_count=1,
        reliability_score=1.0,
    )
    selected = select_jobs_for_node(
        [
            _job(job_id="job-1", priority=90, created_at=now - datetime.timedelta(minutes=3)),
            _job(job_id="job-2", priority=80, created_at=now - datetime.timedelta(minutes=2)),
            _job(job_id="job-3", priority=70, created_at=now - datetime.timedelta(minutes=1)),
        ],
        node,
        [node],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=3,
    )

    assert len(selected) == 1
    assert selected[0].job.job_id == "job-1"


def test_scheduler_filters_out_executor_mismatch() -> None:
    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a", executor="go-native"), active_lease_count=0, reliability_score=1.0)
    selected = select_jobs_for_node(
        [_job(job_id="job-ios", target_executor="swift-native")],
        node,
        [node],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert selected == []


def test_scheduler_filters_out_resource_shortage() -> None:
    now = _utcnow()
    node = build_node_snapshot(
        _node(node_id="node-a", cpu_cores=4, memory_mb=4096, gpu_vram_mb=0, storage_mb=10240),
        active_lease_count=0,
        reliability_score=1.0,
    )
    selected = select_jobs_for_node(
        [
            _job(
                job_id="job-heavy",
                required_cpu_cores=8,
                required_memory_mb=8192,
                required_gpu_vram_mb=4096,
                required_storage_mb=20480,
            )
        ],
        node,
        [node],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert selected == []


def test_scheduler_filters_out_worker_pool_mismatch() -> None:
    now = _utcnow()
    node = build_node_snapshot(
        _node(node_id="node-a", capabilities=["connector.invoke"], worker_pools=["batch"]),
        active_lease_count=0,
        reliability_score=1.0,
    )
    selected = select_jobs_for_node(
        [_job(job_id="job-interactive", kind="connector.invoke")],
        node,
        [node],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
    )

    assert selected == []


def test_scheduler_blocks_low_priority_jobs_that_would_delay_reservation() -> None:
    now = _utcnow()
    node_record = _node(node_id="node-a", max_concurrency=2)
    node = build_node_snapshot(node_record, active_lease_count=0, reliability_score=1.0)
    reservation_mgr = get_reservation_manager()
    reservation_mgr.create_reservation(
        _job(job_id="job-reserved", tenant_id="default", priority=90, estimated_duration_s=120),
        node,
        start_at=now + datetime.timedelta(minutes=2),
    )

    selected = select_jobs_for_node(
        [
            _job(job_id="job-too-long", priority=20, estimated_duration_s=300),
            _job(job_id="job-short", priority=10, estimated_duration_s=60),
        ],
        node,
        [node],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=2,
    )

    assert [item.job.job_id for item in selected] == ["job-short"]


def test_scheduler_honors_time_budgeted_global_plan() -> None:
    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a"), active_lease_count=0, reliability_score=1.0)
    other = build_node_snapshot(_node(node_id="node-b"), active_lease_count=0, reliability_score=1.0)
    selected = select_jobs_for_node(
        [
            _job(job_id="job-self", priority=50),
            _job(job_id="job-other", priority=100),
        ],
        node,
        [node, other],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
        placement_plan={"job-self": "node-a", "job-other": "node-b"},
    )

    assert [item.job.job_id for item in selected] == ["job-self"]


def test_scheduler_can_fallback_when_plan_points_elsewhere() -> None:
    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a"), active_lease_count=0, reliability_score=1.0)
    other = build_node_snapshot(_node(node_id="node-b"), active_lease_count=0, reliability_score=1.0)
    selected = select_jobs_for_node(
        [_job(job_id="job-fallback", priority=80)],
        node,
        [node, other],
        now=now,
        accepted_kinds={"connector.invoke"},
        recent_failed_job_ids=set(),
        limit=1,
        placement_plan={"job-fallback": "node-b"},
    )

    assert [item.job.job_id for item in selected] == ["job-fallback"]


def test_build_time_budgeted_placement_plan_skips_oversized_windows(monkeypatch) -> None:
    from backend.core.scheduling_policy_types import SolverConfig

    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a"), active_lease_count=0, reliability_score=1.0)

    class _GuardSolver:
        def solve(self, *args, **kwargs):
            raise AssertionError("solver should not run when the candidate window exceeds the budget")

    monkeypatch.setattr("backend.core.job_scheduler.get_placement_solver", lambda: _GuardSolver())
    monkeypatch.setattr(
        "backend.core.job_scheduler._get_solver_config",
        lambda: SolverConfig(max_jobs_per_dispatch=1),
    )

    plan = build_time_budgeted_placement_plan(
        [_job(job_id="job-1"), _job(job_id="job-2")],
        [node],
        now=now,
        accepted_kinds={"connector.invoke"},
    )

    assert plan == {}


def test_build_time_budgeted_placement_plan_uses_active_jobs_by_node() -> None:
    now = _utcnow()
    node_a = build_node_snapshot(_node(node_id="node-a"), active_lease_count=0, reliability_score=1.0)
    node_b = build_node_snapshot(_node(node_id="node-b"), active_lease_count=0, reliability_score=1.0)

    plan = build_time_budgeted_placement_plan(
        [_job(job_id="job-batch", batch_key="album-42")],
        [node_a, node_b],
        now=now,
        accepted_kinds={"connector.invoke"},
        active_jobs_by_node={
            "node-a": [
                _job(
                    job_id="job-running",
                    status="leased",
                    batch_key="album-42",
                )
            ]
        },
    )

    assert plan == {"job-batch": "node-a"}


def test_build_time_budgeted_placement_plan_exposes_solver_timeout(monkeypatch) -> None:
    now = _utcnow()
    node = build_node_snapshot(_node(node_id="node-a"), active_lease_count=0, reliability_score=1.0)

    class _TimeoutSolver:
        def solve(self, *args, metrics=None, **kwargs):
            assert metrics is not None
            metrics["timed_out"] = True
            metrics["result"] = "time_budget_exceeded"
            return {}

    monkeypatch.setattr("backend.core.job_scheduler.get_placement_solver", lambda: _TimeoutSolver())

    context: dict[str, object] = {}
    plan = build_time_budgeted_placement_plan(
        [_job(job_id="job-1")],
        [node],
        now=now,
        accepted_kinds={"connector.invoke"},
        decision_context=context,
    )

    assert plan == {}
    assert context["attempted"] is True
    assert context["timed_out"] is True
    assert context["reason"] == "time_budget_exceeded"
