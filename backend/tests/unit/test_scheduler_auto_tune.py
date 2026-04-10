"""Tests for scheduler self-learning auto-tune engine.

Covers AdaptiveWeightStore, Node/Kind/StrategyPerformanceTrackers,
SchedulerTuner orchestrator, GovernanceFacade proxy, lifecycle helpers.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from backend.runtime.scheduling.scheduler_auto_tune import (
    MAX_MULTIPLIER,
    MIN_MULTIPLIER,
    MIN_SAMPLES_BEFORE_ADJUST,
    AdaptiveWeightStore,
    KindPerformanceTracker,
    NodePerformanceTracker,
    OutcomeSignal,
    SchedulerTuner,
    StrategyEffectivenessTracker,
    TuningDimension,
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2025, 7, 1, 12, 0, 0)


def _make_signal(**overrides) -> OutcomeSignal:
    defaults = dict(
        job_id="job-1",
        node_id="node-1",
        kind="shell.exec",
        strategy="spread",
        tenant_id="default",
        score_breakdown={"priority": 80, "age": 20, "scarcity": 0},
        success=True,
        latency_ms=500.0,
        retry_count=0,
        node_utilisation=0.5,
        timestamp=_utcnow(),
    )
    defaults.update(overrides)
    return OutcomeSignal(**defaults)


class TestTuningDimension:
    def test_all_dimensions_are_strings(self) -> None:
        for dim in TuningDimension:
            assert isinstance(dim.value, str)

    def test_expected_dimensions_exist(self) -> None:
        names = {d.value for d in TuningDimension}
        expected = {
            "priority",
            "age",
            "scarcity",
            "reliability",
            "strategy",
            "zone",
            "resource_fit",
            "data_locality",
            "latency",
            "power",
            "thermal",
            "affinity",
            "sla_urgency",
            "batch",
            "load_penalty",
            "freshness_penalty",
            "failure_penalty",
        }
        assert expected <= names, f"missing: {expected - names}"


class TestOutcomeSignal:
    def test_frozen(self) -> None:
        sig = _make_signal()
        with pytest.raises(AttributeError):
            sig.success = False  # type: ignore[misc]

    def test_default_timestamp(self) -> None:
        sig = OutcomeSignal(
            job_id="j",
            node_id="n",
            kind="k",
            strategy="s",
            tenant_id="t",
            score_breakdown={},
            success=True,
            latency_ms=0,
            retry_count=0,
            node_utilisation=0,
        )
        assert isinstance(sig.timestamp, datetime.datetime)


class TestAdaptiveWeightStore:
    def test_initial_multipliers_are_one(self) -> None:
        store = AdaptiveWeightStore()
        for dim in TuningDimension:
            assert store.get(dim.value) == 1.0

    def test_cold_start_returns_one(self) -> None:
        store = AdaptiveWeightStore()
        breakdown = {"priority": 100}
        for _ in range(MIN_SAMPLES_BEFORE_ADJUST - 1):
            store.update(breakdown, success=True)
        assert store.get("priority") == 1.0

    def test_after_threshold_adjusts(self) -> None:
        store = AdaptiveWeightStore()
        breakdown = {"priority": 100}
        for _ in range(MIN_SAMPLES_BEFORE_ADJUST + 10):
            store.update(breakdown, success=True)
        mult = store.get("priority")
        assert mult > 1.0, f"expected > 1.0 after success, got {mult}"

    def test_failures_reduce_multiplier(self) -> None:
        store = AdaptiveWeightStore()
        breakdown = {"priority": 100}
        for _ in range(MIN_SAMPLES_BEFORE_ADJUST + 20):
            store.update(breakdown, success=False)
        mult = store.get("priority")
        assert mult < 1.0, f"expected < 1.0 after failure, got {mult}"

    def test_clamping_upper(self) -> None:
        store = AdaptiveWeightStore(learning_rate=0.5)
        breakdown = {"priority": 100}
        for _ in range(200):
            store.update(breakdown, success=True)
        assert store.get("priority") <= MAX_MULTIPLIER

    def test_clamping_lower(self) -> None:
        store = AdaptiveWeightStore(learning_rate=0.5)
        breakdown = {"priority": 100}
        for _ in range(200):
            store.update(breakdown, success=False)
        assert store.get("priority") >= MIN_MULTIPLIER

    def test_unknown_dimension_returns_one(self) -> None:
        store = AdaptiveWeightStore()
        assert store.get("nonexistent_dimension_xyz") == 1.0

    def test_decay_toward_baseline(self) -> None:
        store = AdaptiveWeightStore()
        breakdown = {"priority": 100}
        for _ in range(MIN_SAMPLES_BEFORE_ADJUST + 30):
            store.update(breakdown, success=True)
        before = store.get("priority")
        assert before > 1.0
        for _ in range(100):
            store.decay_toward_baseline(rate=0.01)
        after = store.get("priority")
        assert after < before, "decay should move toward 1.0"

    def test_snapshot_structure(self) -> None:
        store = AdaptiveWeightStore()
        snap = store.snapshot()
        assert "priority" in snap
        assert "multiplier" in snap["priority"]
        assert "sample_count" in snap["priority"]
        assert "active" in snap["priority"]

    def test_reset_clears_state(self) -> None:
        store = AdaptiveWeightStore()
        for _ in range(MIN_SAMPLES_BEFORE_ADJUST + 10):
            store.update({"priority": 100}, success=True)
        assert store.get("priority") > 1.0
        store.reset()
        assert store.get("priority") == 1.0


class TestNodePerformanceTracker:
    def test_unknown_node_bias_zero(self) -> None:
        tracker = NodePerformanceTracker()
        assert tracker.get_bias("unknown-node") == 0.0

    def test_few_samples_bias_zero(self) -> None:
        tracker = NodePerformanceTracker()
        for _ in range(4):
            tracker.record("node-1", success=True, latency_ms=100)
        assert tracker.get_bias("node-1") == 0.0

    def test_success_positive_bias(self) -> None:
        tracker = NodePerformanceTracker()
        for _ in range(20):
            tracker.record("node-1", success=True, latency_ms=100)
        bias = tracker.get_bias("node-1")
        assert bias > 0, f"expected positive bias, got {bias}"

    def test_failure_negative_bias(self) -> None:
        tracker = NodePerformanceTracker()
        for _ in range(20):
            tracker.record("node-fail", success=False, latency_ms=5000)
        bias = tracker.get_bias("node-fail")
        assert bias < 0, f"expected negative bias, got {bias}"

    def test_bias_bounded(self) -> None:
        tracker = NodePerformanceTracker()
        for _ in range(100):
            tracker.record("node-1", success=True, latency_ms=50)
        assert -20 <= tracker.get_bias("node-1") <= 20

    def test_snapshot(self) -> None:
        tracker = NodePerformanceTracker()
        tracker.record("node-1", success=True, latency_ms=100)
        snap = tracker.snapshot()
        assert "node-1" in snap
        assert "success_rate" in snap["node-1"]
        assert "bias" in snap["node-1"]

    def test_reset(self) -> None:
        tracker = NodePerformanceTracker()
        for _ in range(10):
            tracker.record("node-1", success=True, latency_ms=100)
        tracker.reset()
        assert tracker.get_bias("node-1") == 0.0


class TestKindPerformanceTracker:
    def test_unknown_kind_zero_risk(self) -> None:
        tracker = KindPerformanceTracker()
        assert tracker.get_risk("unknown_kind") == 0.0

    def test_few_samples_zero_risk(self) -> None:
        tracker = KindPerformanceTracker()
        for _ in range(9):
            tracker.record("shell.exec", success=False, latency_ms=100)
        assert tracker.get_risk("shell.exec") == 0.0

    def test_all_failures_high_risk(self) -> None:
        tracker = KindPerformanceTracker()
        for _ in range(30):
            tracker.record("bad_kind", success=False, latency_ms=100)
        risk = tracker.get_risk("bad_kind")
        assert risk > 0.8, f"expected high risk, got {risk}"

    def test_all_successes_low_risk(self) -> None:
        tracker = KindPerformanceTracker()
        for _ in range(30):
            tracker.record("good_kind", success=True, latency_ms=100)
        risk = tracker.get_risk("good_kind")
        assert risk < 0.2, f"expected low risk, got {risk}"

    def test_snapshot(self) -> None:
        tracker = KindPerformanceTracker()
        tracker.record("shell.exec", success=True, latency_ms=100)
        snap = tracker.snapshot()
        assert "shell.exec" in snap
        assert "risk" in snap["shell.exec"]

    def test_reset(self) -> None:
        tracker = KindPerformanceTracker()
        for _ in range(20):
            tracker.record("kind-a", success=False, latency_ms=100)
        tracker.reset()
        assert tracker.get_risk("kind-a") == 0.0


class TestStrategyEffectivenessTracker:
    def test_no_data_recommend_none(self) -> None:
        tracker = StrategyEffectivenessTracker()
        assert tracker.recommend() is None

    def test_insufficient_samples_recommend_none(self) -> None:
        tracker = StrategyEffectivenessTracker()
        for _ in range(29):
            tracker.record("spread", success=True, latency_ms=100)
        assert tracker.recommend() is None

    def test_recommend_best(self) -> None:
        tracker = StrategyEffectivenessTracker()
        for _ in range(50):
            tracker.record("spread", success=True, latency_ms=100)
            tracker.record("binpack", success=False, latency_ms=5000)
        rec = tracker.recommend()
        assert rec == "spread"

    def test_snapshot(self) -> None:
        tracker = StrategyEffectivenessTracker()
        tracker.record("spread", success=True, latency_ms=100)
        snap = tracker.snapshot()
        assert "spread" in snap
        assert "success_rate" in snap["spread"]

    def test_reset(self) -> None:
        tracker = StrategyEffectivenessTracker()
        for _ in range(50):
            tracker.record("spread", success=True, latency_ms=100)
        tracker.reset()
        assert tracker.recommend() is None


class TestSchedulerTuner:
    def test_default_enabled(self) -> None:
        tuner = SchedulerTuner()
        assert tuner.enabled is True

    def test_disabled_returns_neutral(self) -> None:
        tuner = SchedulerTuner(enabled=False)
        tuner.record_outcome(_make_signal())
        assert tuner.get_adjustment("priority") == 1.0
        assert tuner.get_node_bias("node-1") == 0.0
        assert tuner.get_kind_risk("shell.exec") == 0.0

    def test_set_enabled_toggle(self) -> None:
        tuner = SchedulerTuner()
        tuner.set_enabled(False)
        assert tuner.enabled is False
        tuner.set_enabled(True)
        assert tuner.enabled is True

    def test_record_outcome_increments_signals(self) -> None:
        tuner = SchedulerTuner()
        assert tuner._total_signals == 0
        tuner.record_outcome(_make_signal())
        assert tuner._total_signals == 1

    def test_record_outcome_updates_node_tracker(self) -> None:
        tuner = SchedulerTuner()
        for _ in range(10):
            tuner.record_outcome(_make_signal(node_id="n1", success=True))
        snap = tuner.node_tracker.snapshot()
        assert "n1" in snap

    def test_record_outcome_updates_kind_tracker(self) -> None:
        tuner = SchedulerTuner()
        for _ in range(15):
            tuner.record_outcome(_make_signal(kind="scan", success=True))
        snap = tuner.kind_tracker.snapshot()
        assert "scan" in snap

    def test_record_outcome_updates_strategy_tracker(self) -> None:
        tuner = SchedulerTuner()
        for _ in range(10):
            tuner.record_outcome(_make_signal(strategy="binpack"))
        snap = tuner.strategy_tracker.snapshot()
        assert "binpack" in snap

    def test_get_adjustment_cold_start(self) -> None:
        tuner = SchedulerTuner()
        assert tuner.get_adjustment("priority") == 1.0

    def test_get_adjustment_after_learning(self) -> None:
        tuner = SchedulerTuner()
        for _ in range(MIN_SAMPLES_BEFORE_ADJUST + 20):
            tuner.record_outcome(
                _make_signal(
                    score_breakdown={"priority": 100},
                    success=True,
                )
            )
        adj = tuner.get_adjustment("priority")
        assert adj >= 1.0

    def test_decay_moves_toward_baseline(self) -> None:
        tuner = SchedulerTuner()
        for _ in range(MIN_SAMPLES_BEFORE_ADJUST + 30):
            tuner.record_outcome(
                _make_signal(
                    score_breakdown={"priority": 100},
                    success=True,
                )
            )
        before = tuner.get_adjustment("priority")
        for _ in range(50):
            tuner.decay()
        after = tuner.get_adjustment("priority")
        assert after <= before

    def test_snapshot_structure(self) -> None:
        tuner = SchedulerTuner()
        tuner.record_outcome(_make_signal())
        snap = tuner.snapshot()
        assert "enabled" in snap
        assert "total_signals" in snap
        assert "dimension_weights" in snap
        assert "node_performance" in snap
        assert "kind_performance" in snap
        assert "strategy_effectiveness" in snap
        assert "recommended_strategy" in snap
        assert snap["total_signals"] == 1

    def test_reset_clears_all(self) -> None:
        tuner = SchedulerTuner()
        for _ in range(30):
            tuner.record_outcome(_make_signal())
        tuner.reset()
        assert tuner._total_signals == 0
        assert tuner.get_adjustment("priority") == 1.0
        snap = tuner.snapshot()
        assert snap["total_signals"] == 0

    def test_disabled_record_is_noop(self) -> None:
        tuner = SchedulerTuner(enabled=False)
        for _ in range(50):
            tuner.record_outcome(_make_signal())
        assert tuner._total_signals == 0

    def test_recommend_strategy_delegates(self) -> None:
        tuner = SchedulerTuner()
        for _ in range(50):
            tuner.record_outcome(_make_signal(strategy="balanced", success=True))
            tuner.record_outcome(_make_signal(strategy="spread", success=False))
        rec = tuner.recommend_strategy()
        assert rec == "balanced"


class TestGovernanceFacadeTunerProxies:
    def test_tuner_snapshot_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.snapshot.return_value = {"enabled": True}
            mock_get.return_value = mock_tuner
            result = facade.tuner_snapshot()
        assert result == {"enabled": True}
        mock_tuner.snapshot.assert_called_once()

    def test_tuner_enabled_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.enabled = True
            mock_get.return_value = mock_tuner
            assert facade.tuner_enabled() is True

    def test_set_tuner_enabled_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_get.return_value = mock_tuner
            facade.set_tuner_enabled(False)
        mock_tuner.set_enabled.assert_called_once_with(False)

    def test_reset_tuner_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_get.return_value = mock_tuner
            facade.reset_tuner()
        mock_tuner.reset.assert_called_once()

    def test_get_tuner_adjustment_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.get_adjustment.return_value = 1.2
            mock_get.return_value = mock_tuner
            result = facade.get_tuner_adjustment("priority")
        assert result == 1.2
        mock_tuner.get_adjustment.assert_called_once_with("priority")

    def test_get_tuner_node_bias_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.get_node_bias.return_value = 12.5
            mock_get.return_value = mock_tuner
            result = facade.get_tuner_node_bias("n1")
        assert result == 12.5

    def test_get_tuner_kind_risk_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.get_kind_risk.return_value = 0.75
            mock_get.return_value = mock_tuner
            result = facade.get_tuner_kind_risk("bad_kind")
        assert result == 0.75

    def test_tuner_recommend_strategy_delegates(self) -> None:
        from backend.runtime.scheduling.governance_facade import GovernanceFacade

        facade = GovernanceFacade()
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.recommend_strategy.return_value = "binpack"
            mock_get.return_value = mock_tuner
            result = facade.tuner_recommend_strategy()
        assert result == "binpack"


class TestScoringIntegration:
    def test_tuner_multiplier_applied_to_scoring(self) -> None:
        """Verify that score_job_for_node uses tuner adjustments."""
        from backend.runtime.scheduling.job_scoring import score_job_for_node

        job = MagicMock()
        job.priority = 100
        job.created_at = _utcnow() - datetime.timedelta(minutes=10)
        job.target_zone = None
        job.job_id = "j1"
        job.scheduling_strategy = "spread"
        job.data_locality_key = None
        job.power_budget_watts = None
        job.thermal_sensitivity = None
        job.affinity_labels = {}
        job.batch_key = None
        job.target_executor = None
        job.required_cpu_cores = 0
        job.required_memory_mb = 0
        job.required_gpu_vram_mb = 0
        job.required_storage_mb = 0
        job.deadline_at = None
        job.sla_seconds = None

        from backend.runtime.scheduling.job_scheduler import SchedulerNodeSnapshot

        node = SchedulerNodeSnapshot(
            node_id="n1",
            os="linux",
            arch="amd64",
            executor="docker",
            zone="zone-a",
            capabilities=frozenset(),
            accepted_kinds=frozenset({"shell.exec"}),
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

        # Baseline (tuner returns 1.0 for all)
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.get_adjustment.return_value = 1.0
            mock_tuner.get_node_bias.return_value = 0.0
            mock_get.return_value = mock_tuner

            score_base, bd_base = score_job_for_node(
                job,
                node,
                now=_utcnow(),
                total_active_nodes=5,
                eligible_nodes_count=3,
                recent_failed_job_ids=set(),
            )

        # Double priority multiplier
        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()

            def _adj(dim: str) -> float:
                return 2.0 if dim == "priority" else 1.0

            mock_tuner.get_adjustment.side_effect = _adj
            mock_tuner.get_node_bias.return_value = 0.0
            mock_get.return_value = mock_tuner

            score_boosted, bd_boosted = score_job_for_node(
                job,
                node,
                now=_utcnow(),
                total_active_nodes=5,
                eligible_nodes_count=3,
                recent_failed_job_ids=set(),
            )

        assert bd_boosted["priority"] == bd_base["priority"] * 2
        assert score_boosted > score_base

    def test_learned_node_bias_in_breakdown(self) -> None:
        from backend.runtime.scheduling.job_scoring import score_job_for_node

        job = MagicMock()
        job.priority = 50
        job.created_at = _utcnow()
        job.target_zone = None
        job.job_id = "j2"
        job.scheduling_strategy = "spread"
        job.data_locality_key = None
        job.power_budget_watts = None
        job.thermal_sensitivity = None
        job.affinity_labels = {}
        job.batch_key = None
        job.target_executor = None
        job.required_cpu_cores = 0
        job.required_memory_mb = 0
        job.required_gpu_vram_mb = 0
        job.required_storage_mb = 0
        job.deadline_at = None
        job.sla_seconds = None

        from backend.runtime.scheduling.job_scheduler import SchedulerNodeSnapshot

        node = SchedulerNodeSnapshot(
            node_id="n1",
            os="linux",
            arch="amd64",
            executor="docker",
            zone="zone-a",
            capabilities=frozenset(),
            accepted_kinds=frozenset({"shell.exec"}),
            max_concurrency=4,
            active_lease_count=0,
            cpu_cores=8,
            memory_mb=16384,
            gpu_vram_mb=0,
            storage_mb=100000,
            reliability_score=0.90,
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

        with patch(
            "backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner",
        ) as mock_get:
            mock_tuner = MagicMock()
            mock_tuner.get_adjustment.return_value = 1.0
            mock_tuner.get_node_bias.return_value = 15.0
            mock_get.return_value = mock_tuner

            _, breakdown = score_job_for_node(
                job,
                node,
                now=_utcnow(),
                total_active_nodes=5,
                eligible_nodes_count=3,
                recent_failed_job_ids=set(),
            )

        assert "learned_node_bias" in breakdown
        assert breakdown["learned_node_bias"] == 15

    def test_recommended_strategy_is_used_when_job_has_no_explicit_strategy(self) -> None:
        from backend.runtime.scheduling.job_scoring import score_job_for_node
        from backend.runtime.scheduling.scheduling_strategies import SchedulingStrategy

        job = MagicMock()
        job.priority = 60
        job.created_at = _utcnow()
        job.target_zone = None
        job.job_id = "j3"
        job.scheduling_strategy = None
        job.data_locality_key = None
        job.power_budget_watts = None
        job.thermal_sensitivity = None
        job.affinity_labels = {}
        job.batch_key = None
        job.target_executor = None
        job.required_cpu_cores = 0
        job.required_memory_mb = 0
        job.required_gpu_vram_mb = 0
        job.required_storage_mb = 0
        job.deadline_at = None
        job.sla_seconds = None

        from backend.runtime.scheduling.job_scheduler import SchedulerNodeSnapshot

        node = SchedulerNodeSnapshot(
            node_id="n1",
            os="linux",
            arch="amd64",
            executor="docker",
            zone="zone-a",
            capabilities=frozenset(),
            accepted_kinds=frozenset({"shell.exec"}),
            max_concurrency=4,
            active_lease_count=0,
            cpu_cores=8,
            memory_mb=16384,
            gpu_vram_mb=0,
            storage_mb=100000,
            reliability_score=0.90,
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

        with patch("backend.runtime.scheduling.job_scoring.calculate_strategy_score", return_value=37) as mock_strategy_score:
            with patch("backend.runtime.scheduling.scheduler_auto_tune.get_scheduler_tuner") as mock_get:
                mock_tuner = MagicMock()
                mock_tuner.recommend_strategy.return_value = "binpack"
                mock_tuner.get_adjustment.return_value = 1.0
                mock_tuner.get_node_bias.return_value = 0.0
                mock_get.return_value = mock_tuner

                _, breakdown = score_job_for_node(
                    job,
                    node,
                    now=_utcnow(),
                    total_active_nodes=5,
                    eligible_nodes_count=3,
                    recent_failed_job_ids=set(),
                )

        mock_tuner.recommend_strategy.assert_called_once()
        assert breakdown["strategy"] == 37
        assert mock_strategy_score.call_args.args[0] is SchedulingStrategy.BINPACK


class TestSingleton:
    def test_get_scheduler_tuner_returns_same_instance(self) -> None:
        from backend.runtime.scheduling.scheduler_auto_tune import get_scheduler_tuner

        t1 = get_scheduler_tuner()
        t2 = get_scheduler_tuner()
        assert t1 is t2


class TestTunerPersistence:
    """Tests for state_to_dict / load_from_dict (in-memory round-trip)."""

    def _make_tuner(self) -> object:
        from backend.runtime.scheduling.scheduler_auto_tune import SchedulerTuner

        return SchedulerTuner(enabled=True)

    def test_state_to_dict_has_expected_keys(self) -> None:
        tuner = self._make_tuner()
        d = tuner.state_to_dict()
        assert d["v"] == 1
        assert "saved_at" in d
        assert "total_signals" in d
        assert "dimensions" in d

    def test_round_trip_preserves_multiplier(self) -> None:
        from backend.runtime.scheduling.scheduler_auto_tune import SchedulerTuner

        src = SchedulerTuner(enabled=True)
        # Force a multiplier change bypassing cold-start threshold
        src.weights._states["priority"].multiplier = 2.5
        src.weights._states["priority"].sample_count = 999

        d = src.state_to_dict()

        dst = SchedulerTuner(enabled=True)
        dst.load_from_dict(d)
        assert dst.weights._states["priority"].multiplier == 2.5
        assert dst.weights._states["priority"].sample_count == 999

    def test_round_trip_preserves_total_signals(self) -> None:
        from backend.runtime.scheduling.scheduler_auto_tune import SchedulerTuner

        src = SchedulerTuner(enabled=True)
        # Manually bump total_signals so we don't need min_samples
        src._total_signals = 42
        d = src.state_to_dict()
        assert d["total_signals"] == 42

        dst = SchedulerTuner(enabled=True)
        dst.load_from_dict(d)
        assert dst._total_signals == 42

    def test_load_unknown_version_is_skipped(self) -> None:
        from backend.runtime.scheduling.scheduler_auto_tune import SchedulerTuner

        tuner = SchedulerTuner(enabled=True)
        tuner.weights._states["priority"].multiplier = 1.9

        tuner.load_from_dict(
            {
                "v": 99,
                "dimensions": {"priority": {"multiplier": 0.1, "sample_count": 1, "success_rate": 0.0, "contribution_ema": 0.0}},
            }
        )

        # Multiplier should be unchanged because version is unknown
        assert tuner.weights._states["priority"].multiplier == 1.9

    def test_load_unknown_dimension_is_silently_skipped(self) -> None:
        from backend.runtime.scheduling.scheduler_auto_tune import SchedulerTuner

        tuner = SchedulerTuner(enabled=True)
        # This must not raise even when the dimension doesn't exist
        tuner.load_from_dict(
            {
                "v": 1,
                "total_signals": 0,
                "dimensions": {"nonexistent_dimension": {"multiplier": 9.9, "sample_count": 1, "success_rate": 0.0, "contribution_ema": 0.0}},
            }
        )

    def test_reset_after_load_clears_state(self) -> None:
        from backend.runtime.scheduling.scheduler_auto_tune import SchedulerTuner

        tuner = SchedulerTuner(enabled=True)
        tuner.weights._states["priority"].multiplier = 2.0
        tuner.weights._states["priority"].sample_count = 100
        tuner.state_to_dict()  # exercise serialisation before reset

        tuner.reset()
        assert tuner.weights._states["priority"].multiplier == 1.0
        assert tuner.weights._states["priority"].sample_count == 0

    def test_json_round_trip_preserves_data(self) -> None:
        """Verify JSON serialisation/deserialisation preserves floating-point fidelity."""
        import json

        from backend.runtime.scheduling.scheduler_auto_tune import SchedulerTuner

        src = SchedulerTuner(enabled=True)
        src.weights._states["priority"].multiplier = 1.7531
        src.weights._states["priority"].sample_count = 150
        src.weights._states["priority"].success_rate = 0.8234
        src.weights._states["priority"].contribution_ema = 0.1122
        src._total_signals = 300

        blob = json.dumps(src.state_to_dict(), separators=(",", ":"))
        restored = json.loads(blob)

        dst = SchedulerTuner(enabled=True)
        dst.load_from_dict(restored)

        assert dst.weights._states["priority"].multiplier == 1.7531
        assert dst.weights._states["priority"].sample_count == 150
        assert dst.weights._states["priority"].success_rate == 0.8234
        assert dst.weights._states["priority"].contribution_ema == 0.1122
        assert dst._total_signals == 300
