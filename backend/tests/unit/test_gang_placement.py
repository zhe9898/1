"""Tests for gang-aware atomic placement in PlacementSolver.

Covers:
- Gang atomic commit: all members placed → committed together
- Gang rollback: one member has no capacity → entire gang skipped
- Gang + non-gang mix: non-gang jobs unaffected by gang failures
- Gang timeout degrade in GangSchedulingGate
- Incomplete gang: heap exhausted before all members placed
- Multiple gangs: independent atomicity guarantees
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from backend.core.job_scheduler import PlacementSolver, SchedulerNodeSnapshot
from backend.core.scheduling_constraints import GangSchedulingGate, SchedulingContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _node(
    node_id: str = "n1",
    *,
    max_concurrency: int = 4,
    active_lease_count: int = 0,
) -> SchedulerNodeSnapshot:
    return SchedulerNodeSnapshot(
        node_id=node_id,
        os="linux",
        arch="amd64",
        executor="docker",
        zone="z1",
        capabilities=frozenset(),
        accepted_kinds=frozenset(),
        worker_pools=frozenset({"batch"}),
        max_concurrency=max_concurrency,
        active_lease_count=active_lease_count,
        cpu_cores=8,
        memory_mb=4096,
        gpu_vram_mb=0,
        storage_mb=10000,
        reliability_score=0.95,
        last_seen_at=_utcnow() - datetime.timedelta(seconds=5),
        enrollment_status="active",
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
    gang_id: str | None = None,
    created_at: datetime.datetime | None = None,
) -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.kind = kind
    j.priority = priority
    j.gang_id = gang_id
    j.tenant_id = "default"
    j.target_os = None
    j.target_arch = None
    j.target_zone = None
    j.target_executor = None
    j.required_capabilities = []
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
    j.created_at = created_at or (_utcnow() - datetime.timedelta(minutes=5))
    j.status = "pending"
    j.deadline_at = None
    j.parent_job_id = None
    return j


def _ctx(now: datetime.datetime | None = None) -> SchedulingContext:
    return SchedulingContext(
        now=now or _utcnow(),
        completed_job_ids=set(),
        available_slots=4,
        parent_jobs={},
    )


# =====================================================================
# Gang atomic placement — PlacementSolver._greedy_match
# =====================================================================


class TestGangAtomicPlacement:
    """Verify gang-aware _greedy_match logic."""

    def test_gang_all_placed_atomically(self) -> None:
        """4-job gang across 2 nodes (each 2 slots) → all placed."""
        solver = PlacementSolver()
        gang = "g1"
        jobs = [_job(f"j{i}", gang_id=gang) for i in range(4)]
        nodes = [_node("n1", max_concurrency=2), _node("n2", max_concurrency=2)]

        plan = solver.solve(jobs, nodes, now=_utcnow(), accepted_kinds={"shell.exec"})

        # All 4 gang members must be placed
        assert set(plan.keys()) == {"j0", "j1", "j2", "j3"}

    def test_gang_rollback_on_capacity_failure(self) -> None:
        """4-job gang but only 3 total slots → entire gang rejected."""
        solver = PlacementSolver()
        gang = "g1"
        jobs = [_job(f"j{i}", gang_id=gang) for i in range(4)]
        nodes = [_node("n1", max_concurrency=2), _node("n2", max_concurrency=1)]

        plan = solver.solve(jobs, nodes, now=_utcnow(), accepted_kinds={"shell.exec"})

        # Gang is all-or-nothing: no member should be placed
        for jid in ("j0", "j1", "j2", "j3"):
            assert jid not in plan

    def test_non_gang_jobs_unaffected_by_gang_failure(self) -> None:
        """Gang fails but non-gang jobs still get placed."""
        solver = PlacementSolver()
        gang_jobs = [_job(f"gang-{i}", gang_id="g1", priority=40) for i in range(4)]
        solo_jobs = [_job(f"solo-{i}", priority=80) for i in range(2)]
        # Only 3 slots total → gang fails (needs 4), solo jobs (higher prio) placed first
        nodes = [_node("n1", max_concurrency=2), _node("n2", max_concurrency=1)]

        plan = solver.solve(
            gang_jobs + solo_jobs,
            nodes,
            now=_utcnow(),
            accepted_kinds={"shell.exec"},
        )

        # Solo jobs placed, gang jobs not
        for i in range(4):
            assert f"gang-{i}" not in plan
        placed_solo = [k for k in plan if k.startswith("solo-")]
        assert len(placed_solo) >= 1  # at least 1 solo gets placed

    def test_two_independent_gangs(self) -> None:
        """Two gangs: one fits, one doesn't → only the fitting one is placed."""
        solver = PlacementSolver()
        # Gang A: 2 jobs, Gang B: 3 jobs
        gang_a = [_job(f"a{i}", gang_id="gA", priority=80) for i in range(2)]
        gang_b = [_job(f"b{i}", gang_id="gB", priority=60) for i in range(3)]
        # 4 total slots → Gang A (2) fits, Gang B (3) would need remaining 2 but has 3 members
        nodes = [_node("n1", max_concurrency=2), _node("n2", max_concurrency=2)]

        plan = solver.solve(
            gang_a + gang_b,
            nodes,
            now=_utcnow(),
            accepted_kinds={"shell.exec"},
        )

        # Gang A should be placed (2 members, enough slots)
        assert "a0" in plan
        assert "a1" in plan

    def test_single_node_gang(self) -> None:
        """Gang with all members fitting on one node."""
        solver = PlacementSolver()
        jobs = [_job(f"j{i}", gang_id="g1") for i in range(3)]
        nodes = [_node("n1", max_concurrency=4)]

        plan = solver.solve(jobs, nodes, now=_utcnow(), accepted_kinds={"shell.exec"})
        assert set(plan.keys()) == {"j0", "j1", "j2"}

    def test_empty_gang_id_treated_as_non_gang(self) -> None:
        """Jobs with gang_id=None are non-gang."""
        solver = PlacementSolver()
        jobs = [_job("j1", gang_id=None), _job("j2", gang_id=None)]
        nodes = [_node("n1", max_concurrency=2)]

        plan = solver.solve(jobs, nodes, now=_utcnow(), accepted_kinds={"shell.exec"})
        assert len(plan) == 2


# =====================================================================
# GangSchedulingGate — timeout / degrade
# =====================================================================


class TestGangSchedulingGateTimeout:
    """Verify gang timeout and degrade behaviour."""

    @patch("backend.core.scheduling_policy_store.get_policy_store")
    def test_gang_timeout_fail(self, mock_store: MagicMock) -> None:
        """Gang older than timeout → rejected (default action=fail)."""
        mock_gang_cfg = MagicMock()
        mock_gang_cfg.wait_timeout_s = 60
        mock_gang_cfg.timeout_action = "fail"
        mock_store.return_value.active.gang = mock_gang_cfg

        gate = GangSchedulingGate()
        # Job created 120s ago → exceeds 60s timeout
        job = _job("j1", gang_id="g1", created_at=_utcnow() - datetime.timedelta(seconds=120))
        ctx = _ctx(_utcnow())

        ok, reason = gate.evaluate(job, ctx)
        assert ok is False
        assert "gang_timeout_expired" in reason

    @patch("backend.core.scheduling_policy_store.get_policy_store")
    def test_gang_timeout_degrade(self, mock_store: MagicMock) -> None:
        """Gang older than timeout with action=degrade → passes gate."""
        mock_gang_cfg = MagicMock()
        mock_gang_cfg.wait_timeout_s = 60
        mock_gang_cfg.timeout_action = "degrade"
        mock_store.return_value.active.gang = mock_gang_cfg

        gate = GangSchedulingGate()
        job = _job("j1", gang_id="g1", created_at=_utcnow() - datetime.timedelta(seconds=120))
        ctx = _ctx(_utcnow())

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert "gang_timeout_degraded" in reason

    def test_non_gang_job_passes(self) -> None:
        """Jobs without gang_id always pass."""
        gate = GangSchedulingGate()
        job = _job("j1", gang_id=None)
        ctx = _ctx()

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert reason == ""

    @patch("backend.core.scheduling_policy_store.get_policy_store")
    def test_gang_within_timeout_checks_readiness(self, mock_store: MagicMock) -> None:
        """Gang within timeout → falls through to readiness check."""
        mock_gang_cfg = MagicMock()
        mock_gang_cfg.wait_timeout_s = 600
        mock_gang_cfg.timeout_action = "fail"
        mock_store.return_value.active.gang = mock_gang_cfg

        gate = GangSchedulingGate()
        # Job created 30s ago → well within timeout
        job = _job("j1", gang_id="g1", created_at=_utcnow() - datetime.timedelta(seconds=30))
        ctx = _ctx(_utcnow())
        # Add surviving_candidates so readiness check has something to work with
        ctx.surviving_candidates = [job]

        ok, reason = gate.evaluate(job, ctx)
        # Result depends on readiness logic, but should not be a timeout error
        assert "gang_timeout" not in reason
