"""Tests for PlacementSolver — global constraint-satisfaction optimiser."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from backend.kernel.scheduling.job_scheduler import (
    PlacementCandidate,
    PlacementSolver,
    SchedulerNodeSnapshot,
    get_placement_solver,
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2025, 1, 15, 12, 0, 0)


def _node(
    node_id: str = "n1",
    *,
    max_concurrency: int = 4,
    active_lease_count: int = 0,
    os: str = "linux",
    arch: str = "amd64",
    executor: str = "docker",
    zone: str | None = "z1",
    capabilities: frozenset[str] | None = None,
    accepted_kinds: frozenset[str] | None = None,
) -> SchedulerNodeSnapshot:
    return SchedulerNodeSnapshot(
        node_id=node_id,
        os=os,
        arch=arch,
        executor=executor,
        zone=zone,
        capabilities=capabilities or frozenset(),
        accepted_kinds=accepted_kinds or frozenset(),
        max_concurrency=max_concurrency,
        active_lease_count=active_lease_count,
        cpu_cores=8,
        memory_mb=4096,
        gpu_vram_mb=0,
        storage_mb=10000,
        reliability_score=0.95,
        last_seen_at=_utcnow() - datetime.timedelta(seconds=5),
        enrollment_status="approved",
        status="online",
        drain_status="active",
        network_latency_ms=10,
        bandwidth_mbps=100,
        cached_data_keys=frozenset(),
        power_capacity_watts=100,
        current_power_watts=50,
        thermal_state="normal",
        cloud_connectivity="online",
        metadata_json={},
    )


def _job(
    job_id: str = "j1",
    *,
    kind: str = "shell.exec",
    priority: int = 50,
    tenant_id: str = "default",
    required_capabilities: list[str] | None = None,
    gang_id: str | None = None,
) -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.kind = kind
    j.priority = priority
    j.tenant_id = tenant_id
    j.target_os = None
    j.target_arch = None
    j.target_zone = None
    j.target_executor = None
    j.required_capabilities = required_capabilities or []
    j.required_cpu_cores = 0
    j.required_memory_mb = 0
    j.required_gpu_vram_mb = 0
    j.required_storage_mb = 0
    j.max_network_latency_ms = None
    j.data_locality_key = None
    j.prefer_cached_data = False
    j.power_budget_watts = None
    j.thermal_sensitivity = None
    j.cloud_fallback_enabled = False
    j.affinity_rules = None
    j.sla_seconds = None
    j.estimated_duration_s = None
    j.started_at = None
    j.created_at = _utcnow() - datetime.timedelta(minutes=5)
    j.status = "pending"
    j.gang_id = gang_id
    return j


class TestPlacementSolver:
    def test_empty_inputs(self) -> None:
        solver = PlacementSolver()
        assert solver.solve([], [], now=_utcnow(), accepted_kinds=set()) == {}

    def test_single_job_single_node(self) -> None:
        solver = PlacementSolver()
        j = _job("j1")
        n = _node("n1")
        plan = solver.solve([j], [n], now=_utcnow(), accepted_kinds={"shell.exec"})
        assert plan == {"j1": "n1"}

    def test_multiple_jobs_spread_across_nodes(self) -> None:
        solver = PlacementSolver()
        jobs = [_job(f"j{i}") for i in range(3)]
        nodes = [_node(f"n{i}") for i in range(3)]
        plan = solver.solve(
            jobs,
            nodes,
            now=_utcnow(),
            accepted_kinds={"shell.exec"},
        )
        # All jobs should be placed
        assert len(plan) == 3
        assert set(plan.keys()) == {"j0", "j1", "j2"}

    def test_respects_capacity(self) -> None:
        solver = PlacementSolver()
        jobs = [_job(f"j{i}") for i in range(5)]
        nodes = [_node("n1", max_concurrency=2), _node("n2", max_concurrency=2)]
        plan = solver.solve(
            jobs,
            nodes,
            now=_utcnow(),
            accepted_kinds={"shell.exec"},
        )
        # Only 4 jobs can fit (2 + 2)
        assert len(plan) <= 4

    def test_ineligible_nodes_excluded(self) -> None:
        solver = PlacementSolver()
        j = _job("j1")
        n = _node("n1", max_concurrency=4, active_lease_count=4)  # full
        plan = solver.solve([j], [n], now=_utcnow(), accepted_kinds={"shell.exec"})
        assert len(plan) == 0

    def test_global_adjustments_scoring(self) -> None:
        solver = PlacementSolver()
        candidates = [
            PlacementCandidate(job=_job("j1"), node=_node("n1", active_lease_count=0), score=100),
            PlacementCandidate(job=_job("j1"), node=_node("n2", active_lease_count=3, max_concurrency=4), score=100),
        ]
        nodes = [_node("n1"), _node("n2", active_lease_count=3, max_concurrency=4)]
        solver._apply_global_adjustments(candidates, nodes)
        # n1 is underloaded → should get spread bonus
        assert candidates[0].breakdown.get("solver_spread", 0) > 0

    def test_large_simple_batch_fast_path_accepts_uniform_resource_requests(self) -> None:
        solver = PlacementSolver()
        jobs = [_job(f"j{i}") for i in range(96)]
        for job in jobs:
            job.required_cpu_cores = 1
            job.required_memory_mb = 512
        nodes = [_node(f"n{i}", max_concurrency=2) for i in range(48)]
        metrics: dict[str, object] = {}

        plan = solver.solve(
            jobs,
            nodes,
            now=_utcnow(),
            accepted_kinds={"shell.exec"},
            metrics=metrics,
        )

        assert len(plan) == len(jobs)
        assert metrics.get("result") == "fast_path_planned"

    def test_sparse_capability_prefilter_reduces_candidate_matrix(self) -> None:
        solver = PlacementSolver()
        metrics: dict[str, object] = {}
        jobs = [_job("gpu-job", required_capabilities=["gpu"])]
        nodes = [
            _node("gpu-node", capabilities=frozenset({"gpu"})),
            _node("cpu-node", capabilities=frozenset({"cpu"})),
        ]

        plan = solver.solve(
            jobs,
            nodes,
            now=_utcnow(),
            accepted_kinds={"shell.exec"},
            metrics=metrics,
        )

        assert plan == {"gpu-job": "gpu-node"}
        assert metrics.get("candidate_pairs_sparse") == 1
        assert metrics.get("feasible_pairs") == 1

    def test_grouped_gang_assignment_remains_atomic(self) -> None:
        solver = PlacementSolver()
        jobs = [
            _job("g1", priority=100, gang_id="gang-a"),
            _job("g2", priority=95, gang_id="gang-a"),
        ]
        nodes = [
            _node("n1", max_concurrency=1),
            _node("n2", max_concurrency=1),
        ]

        plan = solver.solve(
            jobs,
            nodes,
            now=_utcnow(),
            accepted_kinds={"shell.exec"},
        )

        assert set(plan) == {"g1", "g2"}
        assert len(set(plan.values())) == 2

    def test_singleton(self) -> None:
        s1 = get_placement_solver()
        s2 = get_placement_solver()
        assert s1 is s2
