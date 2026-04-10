"""Tests for scheduling resilience — 5 industry-standard gap-fill capabilities.

Covers:
- TopologySpreadPolicy (K8s TopologySpreadConstraints)
- PreemptionBudgetPolicy (K8s PodDisruptionBudget)
- SchedulingBackoff (K8s unschedulable-backoff)
- AdmissionController (K8s ResourceQuota)
- SchedulingMetrics (K8s scheduler-metrics)
"""

from __future__ import annotations

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.runtime.scheduling.scheduling_resilience import (
    AdmissionController,
    PreemptionBudgetPolicy,
    SchedulingBackoff,
    SchedulingMetrics,
    TopologySpreadPolicy,
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _make_node_snapshot(**overrides):
    from backend.runtime.scheduling.job_scheduler import SchedulerNodeSnapshot

    defaults = dict(
        node_id="node-1",
        os="linux",
        arch="amd64",
        executor="docker",
        zone="zone-a",
        capabilities=frozenset(),
        accepted_kinds=frozenset({"shell.exec", "connector.invoke"}),
        max_concurrency=4,
        active_lease_count=0,
        cpu_cores=8,
        memory_mb=16384,
        gpu_vram_mb=0,
        storage_mb=100000,
        reliability_score=0.95,
        last_seen_at=_utcnow(),
        enrollment_status="approved",
        status="online",
        drain_status="active",
        network_latency_ms=5,
        bandwidth_mbps=1000,
        cached_data_keys=frozenset(),
        power_capacity_watts=500,
        current_power_watts=200,
        thermal_state="normal",
        cloud_connectivity="online",
        metadata_json={},
    )
    defaults.update(overrides)
    return SchedulerNodeSnapshot(**defaults)


def _make_job(**overrides):
    from backend.models.job import Job

    now = _utcnow()
    defaults = dict(
        tenant_id="default",
        job_id="job-1",
        kind="shell.exec",
        status="pending",
        priority=50,
        target_os=None,
        target_arch=None,
        required_capabilities=[],
        target_zone=None,
        timeout_seconds=300,
        max_retries=0,
        retry_count=0,
        source="console",
        payload={},
        lease_seconds=30,
        attempt=0,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    j = Job()
    for k, v in defaults.items():
        setattr(j, k, v)
    return j


# =====================================================================
# TopologySpreadPolicy
# =====================================================================


class TestTopologySpreadPolicy:
    def setup_method(self):
        TopologySpreadPolicy._zone_context.set(None)

    def test_no_penalty_when_no_zone_context(self):
        policy = TopologySpreadPolicy(max_skew=2)
        node = _make_node_snapshot(zone="zone-a")
        job = _make_job()
        score, bd = policy.adjust_score(job, node, 100, {})
        assert score == 100

    def test_no_penalty_when_node_has_no_zone(self):
        TopologySpreadPolicy.configure_zone_context({"zone-a": 10, "zone-b": 5})
        policy = TopologySpreadPolicy(max_skew=2)
        node = _make_node_snapshot(zone=None)
        job = _make_job()
        score, bd = policy.adjust_score(job, node, 100, {})
        assert score == 100

    def test_no_penalty_when_single_zone(self):
        TopologySpreadPolicy.configure_zone_context({"zone-a": 10})
        policy = TopologySpreadPolicy(max_skew=2)
        node = _make_node_snapshot(zone="zone-a")
        job = _make_job()
        score, bd = policy.adjust_score(job, node, 100, {})
        assert score == 100

    def test_no_penalty_when_balanced(self):
        TopologySpreadPolicy.configure_zone_context({"zone-a": 5, "zone-b": 5})
        policy = TopologySpreadPolicy(max_skew=2)
        node = _make_node_snapshot(zone="zone-a")
        job = _make_job()
        score, bd = policy.adjust_score(job, node, 100, {})
        assert score == 100

    def test_penalty_when_skew_exceeds_max(self):
        # zone-a=10, zone-b=2 → avg=6, skew for zone-a=4, exceeds max_skew=2
        TopologySpreadPolicy.configure_zone_context({"zone-a": 10, "zone-b": 2})
        policy = TopologySpreadPolicy(max_skew=2, penalty_per_skew=8)
        node = _make_node_snapshot(zone="zone-a")
        job = _make_job()
        score, bd = policy.adjust_score(job, node, 100, {})
        # skew = 10 - 6 = 4, excess = 4 - 2 = 2, penalty = 2 * 8 = 16
        assert score == 84
        assert bd.get("topology_spread_penalty") == -16

    def test_penalty_capped_at_max(self):
        TopologySpreadPolicy.configure_zone_context({"zone-a": 100, "zone-b": 0})
        policy = TopologySpreadPolicy(max_skew=2, penalty_per_skew=8, max_penalty=40)
        node = _make_node_snapshot(zone="zone-a")
        job = _make_job()
        score, _ = policy.adjust_score(job, node, 100, {})
        assert score == 60  # 100 - 40

    def test_no_penalty_for_low_zone(self):
        TopologySpreadPolicy.configure_zone_context({"zone-a": 10, "zone-b": 2})
        policy = TopologySpreadPolicy(max_skew=2)
        node = _make_node_snapshot(zone="zone-b")
        job = _make_job()
        score, _ = policy.adjust_score(job, node, 100, {})
        assert score == 100  # zone-b is below average, no penalty

    def test_rerank_passthrough(self):
        policy = TopologySpreadPolicy()
        node = _make_node_snapshot()
        assert policy.rerank([], node) == []

    def test_accept_always_true(self):
        policy = TopologySpreadPolicy()
        ok, _ = policy.accept(_make_job(), _make_node_snapshot(), 100)
        assert ok is True

    def test_configure_zone_context_updates_class_state(self):
        TopologySpreadPolicy.configure_zone_context({"zone-a": 3, "zone-b": 7})
        _zone_load, _avg_zone_load, _zone_count = TopologySpreadPolicy.get_zone_context_snapshot()
        assert _zone_count == 2
        assert _avg_zone_load == 5.0
        assert _zone_load == {"zone-a": 3, "zone-b": 7}

    @pytest.mark.asyncio
    async def test_context_isolation_across_concurrent_tasks(self):
        async def _score(zone_load: dict[str, int]) -> int:
            TopologySpreadPolicy.configure_zone_context(zone_load)
            await asyncio.sleep(0)
            policy = TopologySpreadPolicy(max_skew=0, penalty_per_skew=10, max_penalty=100)
            node = _make_node_snapshot(zone="zone-a")
            score, _ = policy.adjust_score(_make_job(), node, 100, {})
            return score

        score_overloaded, score_underloaded = await asyncio.gather(
            _score({"zone-a": 10, "zone-b": 0}),
            _score({"zone-a": 0, "zone-b": 10}),
        )
        assert score_overloaded < score_underloaded


# =====================================================================
# PreemptionBudgetPolicy
# =====================================================================


class TestPreemptionBudgetPolicy:
    def setup_method(self):
        PreemptionBudgetPolicy.reset()
        PreemptionBudgetPolicy.configure(max_per_window=5, window_s=300)

    def test_can_preempt_when_budget_is_fresh(self):
        now = _utcnow()
        ok, reason = PreemptionBudgetPolicy.can_preempt(now)
        assert ok is True
        assert reason == ""

    def test_can_preempt_up_to_budget(self):
        now = _utcnow()
        for _ in range(4):
            ok, _ = PreemptionBudgetPolicy.can_preempt(now)
            assert ok is True
            PreemptionBudgetPolicy.record_preemption(now)

        # 5th should still be OK (budget is 5 max, we have 4)
        ok, _ = PreemptionBudgetPolicy.can_preempt(now)
        assert ok is True

    def test_cannot_preempt_when_budget_exhausted(self):
        now = _utcnow()
        for _ in range(5):
            PreemptionBudgetPolicy.record_preemption(now)

        ok, reason = PreemptionBudgetPolicy.can_preempt(now)
        assert ok is False
        assert "preemption_budget_exhausted" in reason

    def test_budget_recovers_after_window(self):
        now = _utcnow()
        old = now - datetime.timedelta(seconds=301)
        for _ in range(5):
            PreemptionBudgetPolicy.record_preemption(old)

        # Now all preemptions are outside the window
        ok, _ = PreemptionBudgetPolicy.can_preempt(now)
        assert ok is True

    def test_recent_count(self):
        now = _utcnow()
        PreemptionBudgetPolicy.record_preemption(now)
        PreemptionBudgetPolicy.record_preemption(now)
        assert PreemptionBudgetPolicy.recent_count(now) == 2

    def test_reset_clears_all(self):
        now = _utcnow()
        PreemptionBudgetPolicy.record_preemption(now)
        PreemptionBudgetPolicy.reset()
        assert PreemptionBudgetPolicy.recent_count(now) == 0

    def test_configure_custom_budget(self):
        now = _utcnow()
        PreemptionBudgetPolicy.configure(max_per_window=2, window_s=60)
        PreemptionBudgetPolicy.record_preemption(now)
        PreemptionBudgetPolicy.record_preemption(now)
        ok, _ = PreemptionBudgetPolicy.can_preempt(now)
        assert ok is False

    def test_thread_safe_recording_under_concurrency(self):
        now = _utcnow()

        def _record_many(n: int) -> None:
            for _ in range(n):
                PreemptionBudgetPolicy.record_preemption(now)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_record_many, 20) for _ in range(4)]
            for future in futures:
                future.result()

        assert PreemptionBudgetPolicy.recent_count(now) == 80


# =====================================================================
# SchedulingBackoff
# =====================================================================


class TestSchedulingBackoff:
    def setup_method(self):
        SchedulingBackoff.reset()
        SchedulingBackoff.BASE_DELAY_S = 5.0
        SchedulingBackoff.MAX_DELAY_S = 300.0
        SchedulingBackoff.MAX_ATTEMPTS = 50

    def test_should_skip_returns_false_for_unknown_job(self):
        now = _utcnow()
        assert SchedulingBackoff.should_skip("unknown-job", now) is False

    def test_record_failure_sets_backoff(self):
        now = _utcnow()
        SchedulingBackoff.record_failure("job-1", now)
        # First failure: delay = min(5 * 2^0, 300) = 5s
        still_in_backoff = now + datetime.timedelta(seconds=2)
        assert SchedulingBackoff.should_skip("job-1", still_in_backoff) is True

    def test_backoff_expires(self):
        now = _utcnow()
        SchedulingBackoff.record_failure("job-1", now)
        after_backoff = now + datetime.timedelta(seconds=6)
        assert SchedulingBackoff.should_skip("job-1", after_backoff) is False

    def test_exponential_growth(self):
        now = _utcnow()
        # First failure: 5s, second: 10s, third: 20s
        SchedulingBackoff.record_failure("job-1", now)
        attempts_1, next_1 = SchedulingBackoff.get_info("job-1")
        assert attempts_1 == 1
        delay_1 = (next_1 - now).total_seconds()
        assert delay_1 == pytest.approx(5.0)

        SchedulingBackoff.record_failure("job-1", now)
        attempts_2, next_2 = SchedulingBackoff.get_info("job-1")
        assert attempts_2 == 2
        delay_2 = (next_2 - now).total_seconds()
        assert delay_2 == pytest.approx(10.0)

        SchedulingBackoff.record_failure("job-1", now)
        _, next_3 = SchedulingBackoff.get_info("job-1")
        delay_3 = (next_3 - now).total_seconds()
        assert delay_3 == pytest.approx(20.0)

    def test_delay_capped_at_max(self):
        now = _utcnow()
        # Push attempts high to trigger cap
        for _ in range(20):
            SchedulingBackoff.record_failure("job-1", now)
        _, next_try = SchedulingBackoff.get_info("job-1")
        delay = (next_try - now).total_seconds()
        assert delay <= SchedulingBackoff.MAX_DELAY_S

    def test_record_success_clears_state(self):
        now = _utcnow()
        SchedulingBackoff.record_failure("job-1", now)
        SchedulingBackoff.record_success("job-1")
        assert SchedulingBackoff.should_skip("job-1", now) is False
        attempts, next_try = SchedulingBackoff.get_info("job-1")
        assert attempts == 0
        assert next_try is None

    def test_reset(self):
        now = _utcnow()
        SchedulingBackoff.record_failure("job-1", now)
        SchedulingBackoff.record_failure("job-2", now)
        SchedulingBackoff.reset()
        assert SchedulingBackoff.should_skip("job-1", now) is False
        assert SchedulingBackoff.should_skip("job-2", now) is False

    def test_cleanup_removes_stale_entries(self):
        now = _utcnow()
        old_time = now - datetime.timedelta(seconds=SchedulingBackoff.MAX_DELAY_S * 3)
        SchedulingBackoff.record_failure("stale-job", old_time)
        SchedulingBackoff._cleanup(now)
        assert "stale-job" not in SchedulingBackoff._entries

    def test_multiple_jobs_independent(self):
        now = _utcnow()
        SchedulingBackoff.record_failure("job-a", now)
        SchedulingBackoff.record_failure("job-b", now)
        SchedulingBackoff.record_success("job-a")
        # job-a cleared, job-b still in backoff
        assert SchedulingBackoff.should_skip("job-a", now) is False
        assert SchedulingBackoff.should_skip("job-b", now + datetime.timedelta(seconds=2)) is True

    def test_thread_safe_backoff_updates(self):
        now = _utcnow()
        job_ids = [f"job-{idx}" for idx in range(40)]

        def _record_all() -> None:
            for job_id in job_ids:
                SchedulingBackoff.record_failure(job_id, now)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_record_all) for _ in range(4)]
            for future in futures:
                future.result()

        for job_id in job_ids:
            attempts, next_try = SchedulingBackoff.get_info(job_id)
            assert attempts == 4
            assert next_try is not None


# =====================================================================
# AdmissionController
# =====================================================================


class TestAdmissionController:
    def setup_method(self):
        AdmissionController.DEFAULT_MAX_PENDING_PER_TENANT = 1000
        AdmissionController.DEFAULT_MAX_TOTAL_ACTIVE = 10_000

    @pytest.mark.asyncio
    async def test_admitted_when_below_limit(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 100
        mock_db.execute.return_value = mock_result

        admitted, reason, details = await AdmissionController.check_admission(
            mock_db,
            "tenant-a",
            max_pending=1000,
        )
        assert admitted is True
        assert reason == ""
        assert details["current"] == 100

    @pytest.mark.asyncio
    async def test_rejected_when_at_limit(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1000
        mock_db.execute.return_value = mock_result

        admitted, reason, details = await AdmissionController.check_admission(
            mock_db,
            "tenant-a",
            max_pending=1000,
        )
        assert admitted is False
        assert "queue_depth_exceeded" in reason
        assert details["current"] == 1000

    @pytest.mark.asyncio
    async def test_rejected_when_over_limit(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1500
        mock_db.execute.return_value = mock_result

        admitted, reason, details = await AdmissionController.check_admission(
            mock_db,
            "tenant-a",
            max_pending=1000,
        )
        assert admitted is False
        assert details["limit"] == 1000

    @pytest.mark.asyncio
    async def test_uses_default_limit_when_none(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_db.execute.return_value = mock_result

        admitted, _, details = await AdmissionController.check_admission(
            mock_db,
            "tenant-a",
        )
        assert admitted is True
        assert details["limit"] == AdmissionController.DEFAULT_MAX_PENDING_PER_TENANT

    @pytest.mark.asyncio
    async def test_zero_active_jobs_allowed(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_db.execute.return_value = mock_result

        admitted, _, _ = await AdmissionController.check_admission(
            mock_db,
            "tenant-a",
            max_pending=5,
        )
        assert admitted is True

    @pytest.mark.asyncio
    async def test_null_count_treated_as_zero(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_db.execute.return_value = mock_result

        admitted, _, details = await AdmissionController.check_admission(
            mock_db,
            "tenant-a",
        )
        assert admitted is True
        assert details["current"] == 0


# =====================================================================
# SchedulingMetrics
# =====================================================================


class TestSchedulingMetrics:
    def setup_method(self):
        SchedulingMetrics.reset()

    def test_record_placement(self):
        SchedulingMetrics.record_placement(42.5)
        snap = SchedulingMetrics.snapshot(window_seconds=60)
        assert snap["placements"] == 1
        assert snap["avg_scheduling_latency_ms"] == pytest.approx(42.5)

    def test_record_rejection(self):
        SchedulingMetrics.record_rejection("no_eligible_slot")
        snap = SchedulingMetrics.snapshot(window_seconds=60)
        assert snap["rejections"] == 1
        assert "no_eligible_slot" in snap["top_rejection_reasons"]

    def test_record_backoff_skip(self):
        SchedulingMetrics.record_backoff_skip()
        SchedulingMetrics.record_backoff_skip()
        snap = SchedulingMetrics.snapshot()
        assert snap["backoff_skips_total"] == 2

    def test_record_admission_rejection(self):
        SchedulingMetrics.record_admission_rejection()
        snap = SchedulingMetrics.snapshot()
        assert snap["admission_rejections_total"] == 1

    def test_record_preemption_budget_hit(self):
        SchedulingMetrics.record_preemption_budget_hit()
        snap = SchedulingMetrics.snapshot()
        assert snap["preemption_budget_hits_total"] == 1

    def test_p95_latency(self):
        for i in range(100):
            SchedulingMetrics.record_placement(float(i))
        snap = SchedulingMetrics.snapshot(window_seconds=60)
        assert snap["p95_scheduling_latency_ms"] >= 90.0

    def test_rejection_rate(self):
        for _ in range(3):
            SchedulingMetrics.record_placement(10.0)
        SchedulingMetrics.record_rejection("no_slot")
        snap = SchedulingMetrics.snapshot(window_seconds=60)
        assert snap["rejection_rate"] == pytest.approx(0.25, abs=0.01)

    def test_placements_per_minute(self):
        for _ in range(6):
            SchedulingMetrics.record_placement(5.0)
        snap = SchedulingMetrics.snapshot(window_seconds=60)
        assert snap["placements_per_minute"] == pytest.approx(6.0)

    def test_empty_snapshot(self):
        snap = SchedulingMetrics.snapshot()
        assert snap["placements"] == 0
        assert snap["rejections"] == 0
        assert snap["avg_scheduling_latency_ms"] == 0.0
        assert snap["p95_scheduling_latency_ms"] == 0.0
        assert snap["rejection_rate"] == 0.0
        assert snap["placements_per_minute"] == 0.0

    def test_reset(self):
        SchedulingMetrics.record_placement(10.0)
        SchedulingMetrics.record_rejection("x")
        SchedulingMetrics.record_backoff_skip()
        SchedulingMetrics.record_admission_rejection()
        SchedulingMetrics.record_preemption_budget_hit()
        SchedulingMetrics.reset()
        snap = SchedulingMetrics.snapshot()
        assert snap["placements"] == 0
        assert snap["rejections"] == 0
        assert snap["backoff_skips_total"] == 0
        assert snap["admission_rejections_total"] == 0
        assert snap["preemption_budget_hits_total"] == 0

    def test_multiple_rejection_reasons_tracked(self):
        SchedulingMetrics.record_rejection("no_slot")
        SchedulingMetrics.record_rejection("no_slot")
        SchedulingMetrics.record_rejection("backoff")
        snap = SchedulingMetrics.snapshot(window_seconds=60)
        reasons = snap["top_rejection_reasons"]
        assert reasons["no_slot"] == 2
        assert reasons["backoff"] == 1


# =====================================================================
# Integration: TopologySpreadPolicy as PlacementPolicy protocol
# =====================================================================


class TestTopologySpreadPolicyProtocol:
    """Verify TopologySpreadPolicy integrates with CompositePlacementPolicy."""

    def setup_method(self):
        TopologySpreadPolicy._zone_context.set(None)

    def test_registered_in_builtin_policies(self):
        from backend.runtime.scheduling.placement_policy import _BUILTIN_POLICIES

        assert "topology_spread" in _BUILTIN_POLICIES

    def test_works_in_composite(self):
        from backend.runtime.scheduling.placement_policy import CompositePlacementPolicy

        TopologySpreadPolicy.configure_zone_context({"zone-a": 20, "zone-b": 4})
        policy = CompositePlacementPolicy(policies=[TopologySpreadPolicy(max_skew=2)])
        node_a = _make_node_snapshot(zone="zone-a")
        node_b = _make_node_snapshot(zone="zone-b")
        job = _make_job()

        score_a, _ = policy.adjust_score(job, node_a, 100, {})
        score_b, _ = policy.adjust_score(job, node_b, 100, {})
        # zone-a is over-represented, should get a lower score
        assert score_a < score_b
