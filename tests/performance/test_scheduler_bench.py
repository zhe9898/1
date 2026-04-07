"""Scheduling kernel micro-benchmarks (pytest-benchmark).

Measures:
- score_job_for_node() throughput (1000 jobs × 100 nodes)
- PlacementSolver.solve() at 500 jobs × 50 nodes
- SchedulingEngine.run() constraint pipeline at 1000 jobs
- Gang _greedy_match with 100 gangs × 5 members

Run:
    pytest tests/performance/test_scheduler_bench.py -v --benchmark-only
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from backend.core.job_scheduler import PlacementSolver, SchedulerNodeSnapshot

# ---------------------------------------------------------------------------
# Try importing pytest-benchmark; skip entire module if unavailable
# ---------------------------------------------------------------------------
try:
    import pytest_benchmark  # noqa: F401
except ImportError:
    pytest.skip("pytest-benchmark not installed", allow_module_level=True)


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _node(
    node_id: str,
    *,
    max_concurrency: int = 8,
    active_lease_count: int = 0,
) -> SchedulerNodeSnapshot:
    return SchedulerNodeSnapshot(
        node_id=node_id,
        os="linux",
        arch="amd64",
        executor="docker",
        zone="z1",
        capabilities=frozenset({"shell", "docker"}),
        accepted_kinds=frozenset({"shell.exec", "docker.run"}),
        worker_pools=frozenset({"batch"}),
        max_concurrency=max_concurrency,
        active_lease_count=active_lease_count,
        cpu_cores=16,
        memory_mb=32768,
        gpu_vram_mb=0,
        storage_mb=100000,
        reliability_score=0.95,
        last_seen_at=_utcnow() - datetime.timedelta(seconds=5),
        enrollment_status="approved",
        status="online",
        drain_status="active",
        network_latency_ms=10,
        bandwidth_mbps=1000,
        cached_data_keys=frozenset(),
        power_capacity_watts=200,
        current_power_watts=80,
        thermal_state="normal",
        cloud_connectivity="online",
        metadata_json={},
    )


def _job(
    job_id: str,
    *,
    priority: int = 50,
    gang_id: str | None = None,
) -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.kind = "shell.exec"
    j.priority = priority
    j.gang_id = gang_id
    j.tenant_id = "default"
    j.target_os = None
    j.target_arch = None
    j.target_zone = None
    j.target_executor = None
    j.required_capabilities = []
    j.required_cpu_cores = 1
    j.required_memory_mb = 512
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
    j.estimated_duration_s = 300
    j.started_at = None
    j.created_at = _utcnow() - datetime.timedelta(minutes=5)
    j.status = "pending"
    j.deadline_at = None
    j.parent_job_id = None
    return j


# ---------------------------------------------------------------------------
# Pre-built datasets (avoid recreating per iteration)
# ---------------------------------------------------------------------------

_NODES_50 = [_node(f"n{i}") for i in range(50)]
_JOBS_500 = [_job(f"j{i}") for i in range(500)]
_ACCEPTED_KINDS = {"shell.exec", "docker.run"}


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.performance
class TestPlacementSolverBench:
    """PlacementSolver throughput benchmarks."""

    def test_solve_500_jobs_50_nodes(self, benchmark) -> None:  # type: ignore[no-untyped-def]
        """Target: < 50ms for 500 jobs × 50 nodes."""
        solver = PlacementSolver()
        result = benchmark(
            solver.solve,
            _JOBS_500,
            _NODES_50,
            now=_utcnow(),
            accepted_kinds=_ACCEPTED_KINDS,
        )
        assert isinstance(result, dict)
        # At least some jobs should be placed
        assert len(result) > 0

    def test_solve_100_jobs_10_nodes(self, benchmark) -> None:  # type: ignore[no-untyped-def]
        """Smaller scenario — baseline reference."""
        solver = PlacementSolver()
        jobs_100 = _JOBS_500[:100]
        nodes_10 = _NODES_50[:10]
        result = benchmark(
            solver.solve,
            jobs_100,
            nodes_10,
            now=_utcnow(),
            accepted_kinds=_ACCEPTED_KINDS,
        )
        assert isinstance(result, dict)

    def test_solve_gang_100_gangs_5_members(self, benchmark) -> None:  # type: ignore[no-untyped-def]
        """100 gangs × 5 members = 500 gang jobs on 50 nodes (8 slots each = 400)."""
        solver = PlacementSolver()
        gang_jobs = []
        for g in range(100):
            for m in range(5):
                gang_jobs.append(_job(f"g{g}-m{m}", gang_id=f"gang-{g}"))

        result = benchmark(
            solver.solve,
            gang_jobs,
            _NODES_50,
            now=_utcnow(),
            accepted_kinds=_ACCEPTED_KINDS,
        )
        assert isinstance(result, dict)


@pytest.mark.performance
class TestScoringBench:
    """Job scoring throughput benchmarks."""

    def test_score_job_for_node_1000x100(self, benchmark) -> None:  # type: ignore[no-untyped-def]
        """Target: score_job_for_node P99 < 1ms per call."""
        from backend.core.job_scoring import score_job_for_node

        jobs = [_job(f"j{i}") for i in range(1000)]
        nodes = [_node(f"n{i}") for i in range(100)]
        total_active_nodes = len(nodes)
        eligible_nodes_count = len(nodes)
        recent_failed_job_ids: set[str] = set()

        def _score_all() -> int:
            total = 0
            for j in jobs[:10]:  # 10 jobs × 100 nodes = 1000 score calls
                for n in nodes:
                    score, _ = score_job_for_node(
                        j,
                        n,
                        now=_utcnow(),
                        total_active_nodes=total_active_nodes,
                        eligible_nodes_count=eligible_nodes_count,
                        recent_failed_job_ids=recent_failed_job_ids,
                    )
                    total += score
            return total

        result = benchmark(_score_all)
        assert result > 0


@pytest.mark.performance
class TestConstraintPipelineBench:
    """SchedulingEngine.run() throughput benchmark."""

    def test_engine_run_1000_jobs(self, benchmark) -> None:  # type: ignore[no-untyped-def]
        """Target: constraint pipeline < 20ms for 1000 jobs."""
        from backend.core.scheduling_constraints import SchedulingContext, SchedulingEngine

        engine = SchedulingEngine()
        jobs_1000 = [_job(f"j{i}") for i in range(1000)]

        def _run_pipeline() -> list:
            ctx = SchedulingContext(
                now=_utcnow(),
                completed_job_ids=set(),
                available_slots=400,
                parent_jobs={},
            )
            return engine.run(jobs_1000, ctx)

        result = benchmark(_run_pipeline)
        assert isinstance(result, list)
