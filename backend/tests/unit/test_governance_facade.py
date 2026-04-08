"""Tests for GovernanceFacade — the mandatory single entry for the dispatch chain.

Covers:
- Seal / unseal lifecycle
- Sealed state blocks feature flag mutation
- Proxy methods delegate to underlying sub-systems
- Decision logger creation
- Metrics snapshot passthrough
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.kernel.scheduling.governance_facade import GovernanceFacade, get_governance_facade

# ── Seal / Unseal ────────────────────────────────────────────────────


class TestGovernanceSeal:
    def test_initially_unsealed(self):
        facade = GovernanceFacade()
        assert facade.is_sealed is False
        assert facade.seal_reason == ""

    def test_seal_sets_flag_and_reason(self):
        facade = GovernanceFacade()
        facade.seal("post-boot lock")
        assert facade.is_sealed is True
        assert facade.seal_reason == "post-boot lock"

    def test_unseal_clears_flag(self):
        facade = GovernanceFacade()
        facade.seal("locked")
        facade.unseal(operator="admin@zen70")
        assert facade.is_sealed is False
        assert facade.seal_reason == ""

    @pytest.mark.asyncio
    async def test_sealed_blocks_feature_mutation(self):
        facade = GovernanceFacade()
        facade.seal("test-lock")
        db = AsyncMock()
        with pytest.raises(RuntimeError, match="governance is sealed"):
            await facade.set_feature_guarded(db, "some_flag", True)

    @pytest.mark.asyncio
    async def test_unsealed_allows_feature_mutation(self):
        facade = GovernanceFacade()
        db = AsyncMock()
        with patch(
            "backend.kernel.scheduling.scheduling_governance.set_scheduling_feature",
            new_callable=AsyncMock,
        ) as mock_set:
            await facade.set_feature_guarded(db, "test_flag", True)
            mock_set.assert_awaited_once_with(db, "test_flag", True)


# ── Strategy Resolution ──────────────────────────────────────────────


class TestStrategyResolution:
    def test_known_strategy_returned(self):
        facade = GovernanceFacade()
        assert facade.resolve_strategy("binpack") == "binpack"
        assert facade.resolve_strategy("SPREAD") == "spread"
        assert facade.resolve_strategy("performance") == "performance"

    def test_unknown_strategy_falls_back_to_spread(self):
        facade = GovernanceFacade()
        assert facade.resolve_strategy("alien") == "spread"

    def test_none_strategy_falls_back(self):
        facade = GovernanceFacade()
        assert facade.resolve_strategy(None) == "spread"


# ── Preemption Budget ────────────────────────────────────────────────


class TestPreemptionBudget:
    def test_can_preempt_delegates(self):
        facade = GovernanceFacade()
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.PreemptionBudgetPolicy.can_preempt",
            return_value=(True, ""),
        ) as mock_can:
            result = facade.can_preempt(now)
            assert result == (True, "")
            mock_can.assert_called_once_with(now)

    def test_record_preemption_delegates(self):
        facade = GovernanceFacade()
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.PreemptionBudgetPolicy.record_preemption",
        ) as mock_rec:
            facade.record_preemption(now)
            mock_rec.assert_called_once_with(now)


# ── Scheduling Metrics Proxy ─────────────────────────────────────────


class TestSchedulingMetricsProxy:
    def test_record_placement(self):
        facade = GovernanceFacade()
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingMetrics.record_placement",
        ) as mock:
            facade.record_placement_metric(42.5)
            mock.assert_called_once_with(42.5)

    def test_record_rejection(self):
        facade = GovernanceFacade()
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingMetrics.record_rejection",
        ) as mock:
            facade.record_rejection_metric("no_eligible_slot")
            mock.assert_called_once_with("no_eligible_slot")

    def test_record_preemption_budget_hit(self):
        facade = GovernanceFacade()
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingMetrics.record_preemption_budget_hit",
        ) as mock:
            facade.record_preemption_budget_hit()
            mock.assert_called_once()

    def test_record_backoff_skip(self):
        facade = GovernanceFacade()
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingMetrics.record_backoff_skip",
        ) as mock:
            facade.record_backoff_skip_metric()
            mock.assert_called_once()

    def test_metrics_snapshot(self):
        facade = GovernanceFacade()
        fake_snap = {"placements": 10, "rejections": 2}
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingMetrics.snapshot",
            return_value=fake_snap,
        ) as mock:
            result = facade.metrics_snapshot(window_seconds=60)
            assert result == fake_snap
            mock.assert_called_once_with(60)


# ── Scheduling Backoff Proxy ─────────────────────────────────────────


class TestSchedulingBackoffProxy:
    def test_should_skip_backoff(self):
        facade = GovernanceFacade()
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingBackoff.should_skip",
            return_value=True,
        ) as mock:
            result = facade.should_skip_backoff("job-1", now)
            assert result is True
            mock.assert_called_once_with("job-1", now)

    def test_record_backoff_failure(self):
        facade = GovernanceFacade()
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingBackoff.record_failure",
        ) as mock:
            facade.record_backoff_failure("job-2", now)
            mock.assert_called_once_with("job-2", now)

    def test_record_backoff_success(self):
        facade = GovernanceFacade()
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.SchedulingBackoff.record_success",
        ) as mock:
            facade.record_backoff_success("job-3")
            mock.assert_called_once_with("job-3")


# ── Topology Spread Proxy ────────────────────────────────────────────


class TestTopologySpreadProxy:
    def test_configure_zone_context(self):
        facade = GovernanceFacade()
        zone_load = {"zone-a": 5, "zone-b": 3}
        with patch(
            "backend.kernel.scheduling.scheduling_resilience.TopologySpreadPolicy.configure_zone_context",
        ) as mock:
            facade.configure_zone_context(zone_load)
            mock.assert_called_once_with(zone_load)


# ── Decision Logger Factory ──────────────────────────────────────────


class TestDecisionLoggerFactory:
    def test_creates_logger(self):
        facade = GovernanceFacade()
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        with patch(
            "backend.kernel.scheduling.scheduling_governance.SchedulingDecisionLogger",
        ) as MockLogger:
            logger = facade.create_decision_logger("tenant-1", "node-a", now)
            MockLogger.assert_called_once_with(
                tenant_id="tenant-1",
                node_id="node-a",
                now=now,
            )
            assert logger == MockLogger.return_value


# ── Feature Flag Query ───────────────────────────────────────────────


class TestFeatureFlagQuery:
    @pytest.mark.asyncio
    async def test_is_feature_enabled(self):
        facade = GovernanceFacade()
        db = AsyncMock()
        with patch(
            "backend.kernel.scheduling.scheduling_governance.is_scheduling_feature_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock:
            result = await facade.is_feature_enabled(db, "sched.preemption")
            assert result is True
            mock.assert_awaited_once_with(db, "sched.preemption")


# ── Singleton ────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_governance_facade_returns_same_instance(self):
        f1 = get_governance_facade()
        f2 = get_governance_facade()
        assert f1 is f2
