"""Tests for quota-aware fair-share scheduling module.

Covers:
- ResourceUsage / ResourceQuotaLimit / ResourceQuotaAccount data structures
- FairShareCalculator (DRF-inspired allocation)
- QuotaAwareGate (hard constraint)
- FairShareScoreModifier (soft priority adjustment)
- build_quota_accounts (from leased jobs)
- Policy store integration (reads FairShareConfig)
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from backend.kernel.scheduling.quota_aware_scheduling import (
    FairShareCalculator,
    FairShareScoreModifier,
    QuotaAwareGate,
    ResourceQuotaAccount,
    ResourceQuotaLimit,
    ResourceUsage,
    build_quota_accounts,
    load_resource_quotas,
)
from backend.kernel.scheduling.scheduling_constraints import SchedulingContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _make_job(**overrides) -> MagicMock:
    job = MagicMock()
    job.job_id = overrides.get("job_id", "job-1")
    job.tenant_id = overrides.get("tenant_id", "default")
    job.priority = overrides.get("priority", 50)
    job.required_cpu_cores = overrides.get("required_cpu_cores", 4)
    job.required_memory_mb = overrides.get("required_memory_mb", 8192)
    job.required_gpu_vram_mb = overrides.get("required_gpu_vram_mb", 0)
    job.status = overrides.get("status", "leased")
    return job


def _make_ctx(
    now: datetime.datetime | None = None,
    **extra_data: object,
) -> SchedulingContext:
    ctx = SchedulingContext(
        now=now or _utcnow(),
        completed_job_ids=set(),
        available_slots=4,
        parent_jobs={},
    )
    ctx.data.update(extra_data)
    return ctx


# =====================================================================
# ResourceUsage & ResourceQuotaLimit
# =====================================================================


class TestResourceUsage:
    def test_default_zero(self):
        u = ResourceUsage()
        assert u.cpu_cores == 0.0
        assert u.memory_mb == 0.0
        assert u.gpu_vram_mb == 0.0
        assert u.concurrent_jobs == 0


class TestResourceQuotaLimit:
    def test_default_unlimited(self):
        lim = ResourceQuotaLimit()
        assert lim.is_unlimited()

    def test_not_unlimited(self):
        lim = ResourceQuotaLimit(max_cpu_cores=64.0)
        assert not lim.is_unlimited()


# =====================================================================
# ResourceQuotaAccount
# =====================================================================


class TestResourceQuotaAccount:
    def test_would_exceed_unlimited(self):
        acct = ResourceQuotaAccount(tenant_id="t1")
        job = _make_job(required_cpu_cores=100)
        exceeded, reason = acct.would_exceed(job)
        assert exceeded is False

    def test_would_exceed_cpu(self):
        acct = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(cpu_cores=60.0),
            limit=ResourceQuotaLimit(max_cpu_cores=64.0),
        )
        job = _make_job(required_cpu_cores=8)
        exceeded, reason = acct.would_exceed(job)
        assert exceeded is True
        assert "cpu_quota" in reason

    def test_within_quota(self):
        acct = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(cpu_cores=10.0),
            limit=ResourceQuotaLimit(max_cpu_cores=64.0),
        )
        job = _make_job(required_cpu_cores=4)
        exceeded, reason = acct.would_exceed(job)
        assert exceeded is False

    def test_would_exceed_memory(self):
        acct = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(memory_mb=120000.0),
            limit=ResourceQuotaLimit(max_memory_mb=131072.0),
        )
        job = _make_job(required_memory_mb=16384)
        exceeded, reason = acct.would_exceed(job)
        assert exceeded is True
        assert "memory_quota" in reason

    def test_would_exceed_concurrent_jobs(self):
        acct = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(concurrent_jobs=99),
            limit=ResourceQuotaLimit(max_concurrent_jobs=100),
        )
        job = _make_job()
        exceeded, reason = acct.would_exceed(job)
        assert exceeded is False  # 99 + 1 = 100, not exceeding

        acct.usage.concurrent_jobs = 100
        exceeded, reason = acct.would_exceed(job)
        assert exceeded is True
        assert "job_quota" in reason

    def test_record_placement(self):
        acct = ResourceQuotaAccount(tenant_id="t1")
        job = _make_job(required_cpu_cores=4, required_memory_mb=8192)
        acct.record_placement(job)
        assert acct.usage.cpu_cores == 4.0
        assert acct.usage.memory_mb == 8192.0
        assert acct.usage.concurrent_jobs == 1


# =====================================================================
# FairShareCalculator
# =====================================================================


class TestFairShareCalculator:
    def test_empty_accounts(self):
        result = FairShareCalculator.compute_fair_shares({}, ResourceUsage())
        assert result == {}

    def test_single_tenant_uses_everything(self):
        acct = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(cpu_cores=64.0),
        )
        cluster = ResourceUsage(cpu_cores=64.0)
        ratios = FairShareCalculator.compute_fair_shares({"t1": acct}, cluster)
        # Single tenant with weight 1.0, consuming 100% → ratio = 1.0/1.0 = 1.0
        assert abs(ratios["t1"] - 1.0) < 0.01

    def test_two_tenants_equal_use(self):
        acct1 = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(cpu_cores=32.0),
        )
        acct2 = ResourceQuotaAccount(
            tenant_id="t2",
            usage=ResourceUsage(cpu_cores=32.0),
        )
        cluster = ResourceUsage(cpu_cores=64.0)
        ratios = FairShareCalculator.compute_fair_shares(
            {"t1": acct1, "t2": acct2},
            cluster,
        )
        # Each gets 50% of fair share, consumes 50% → ratio = 1.0
        assert abs(ratios["t1"] - 1.0) < 0.01
        assert abs(ratios["t2"] - 1.0) < 0.01

    def test_over_and_under_served(self):
        acct1 = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(cpu_cores=48.0),
        )
        acct2 = ResourceQuotaAccount(
            tenant_id="t2",
            usage=ResourceUsage(cpu_cores=16.0),
        )
        cluster = ResourceUsage(cpu_cores=64.0)
        ratios = FairShareCalculator.compute_fair_shares(
            {"t1": acct1, "t2": acct2},
            cluster,
        )
        # t1 uses 75% but fair share is 50% → over-served (ratio > 1)
        assert ratios["t1"] > 1.0
        # t2 uses 25% but fair share is 50% → under-served (ratio < 1)
        assert ratios["t2"] < 1.0

    def test_zero_usage(self):
        acct = ResourceQuotaAccount(
            tenant_id="t1",
            usage=ResourceUsage(),
        )
        cluster = ResourceUsage(cpu_cores=64.0)
        ratios = FairShareCalculator.compute_fair_shares({"t1": acct}, cluster)
        assert ratios["t1"] == 0.0  # Using nothing → ratio = 0


# =====================================================================
# QuotaAwareGate
# =====================================================================


class TestQuotaAwareGate:
    def test_no_accounts_passes(self):
        gate = QuotaAwareGate()
        job = _make_job()
        ctx = _make_ctx()
        ok, reason = gate.evaluate(job, ctx)
        assert ok is True

    def test_within_quota_passes(self):
        gate = QuotaAwareGate()
        acct = ResourceQuotaAccount(
            tenant_id="default",
            usage=ResourceUsage(cpu_cores=10.0),
            limit=ResourceQuotaLimit(max_cpu_cores=64.0),
        )
        ctx = _make_ctx(_quota_accounts={"default": acct})
        job = _make_job(required_cpu_cores=4)
        ok, reason = gate.evaluate(job, ctx)
        assert ok is True

    def test_exceed_quota_rejected(self):
        gate = QuotaAwareGate()
        acct = ResourceQuotaAccount(
            tenant_id="default",
            usage=ResourceUsage(cpu_cores=62.0),
            limit=ResourceQuotaLimit(max_cpu_cores=64.0),
        )
        ctx = _make_ctx(_quota_accounts={"default": acct})
        job = _make_job(required_cpu_cores=4)
        ok, reason = gate.evaluate(job, ctx)
        assert ok is False
        assert "resource_quota_exceeded" in reason

    def test_unknown_tenant_passes(self):
        gate = QuotaAwareGate()
        acct = ResourceQuotaAccount(
            tenant_id="other",
            limit=ResourceQuotaLimit(max_cpu_cores=10.0),
        )
        ctx = _make_ctx(_quota_accounts={"other": acct})
        job = _make_job(tenant_id="default")
        ok, reason = gate.evaluate(job, ctx)
        assert ok is True

    def test_order_and_hardness(self):
        gate = QuotaAwareGate()
        assert gate.order == 5
        assert gate.hard is True


# =====================================================================
# FairShareScoreModifier
# =====================================================================


class TestFairShareScoreModifier:
    def test_no_ratios_passes(self):
        mod = FairShareScoreModifier()
        job = _make_job(priority=50)
        ctx = _make_ctx()
        ok, reason = mod.evaluate(job, ctx)
        assert ok is True
        assert job.priority == 50

    def test_under_served_gets_boost(self):
        mod = FairShareScoreModifier()
        job = _make_job(priority=50, tenant_id="default")
        ctx = _make_ctx(_fair_share_ratios={"default": 0.5})
        ok, reason = mod.evaluate(job, ctx)
        assert ok is True
        assert job.priority > 50
        assert "fair_share_adj" in reason

    def test_over_served_gets_penalty(self):
        mod = FairShareScoreModifier()
        job = _make_job(priority=50, tenant_id="default")
        ctx = _make_ctx(_fair_share_ratios={"default": 1.5})
        ok, reason = mod.evaluate(job, ctx)
        assert ok is True
        assert job.priority < 50
        assert "fair_share_adj" in reason

    def test_at_fair_share_no_change(self):
        mod = FairShareScoreModifier()
        job = _make_job(priority=50, tenant_id="default")
        ctx = _make_ctx(_fair_share_ratios={"default": 1.0})
        ok, reason = mod.evaluate(job, ctx)
        assert ok is True
        assert job.priority == 50

    def test_within_deadband_no_change(self):
        mod = FairShareScoreModifier()
        job = _make_job(priority=50, tenant_id="default")
        # 1.03 is within default deadband of 0.05
        ctx = _make_ctx(_fair_share_ratios={"default": 1.03})
        ok, reason = mod.evaluate(job, ctx)
        assert ok is True
        assert job.priority == 50

    def test_priority_clamped(self):
        mod = FairShareScoreModifier()
        # Very under-served → large boost, but capped at priority_cap
        job = _make_job(priority=150, tenant_id="default")
        ctx = _make_ctx(_fair_share_ratios={"default": 0.1})
        ok, reason = mod.evaluate(job, ctx)
        assert ok is True
        assert job.priority <= 160  # Default cap

    def test_order_and_hardness(self):
        mod = FairShareScoreModifier()
        assert mod.order == 7
        assert mod.hard is False


# =====================================================================
# build_quota_accounts
# =====================================================================


class TestBuildQuotaAccounts:
    def test_empty_jobs(self):
        accounts = build_quota_accounts([], quotas={})
        assert accounts == {}

    def test_aggregates_resources(self):
        j1 = _make_job(tenant_id="t1", required_cpu_cores=4, required_memory_mb=8192)
        j2 = _make_job(tenant_id="t1", required_cpu_cores=8, required_memory_mb=16384)
        j3 = _make_job(tenant_id="t2", required_cpu_cores=2, required_memory_mb=4096)

        quotas = {
            "t1": ResourceQuotaLimit(max_cpu_cores=64.0),
            "t2": ResourceQuotaLimit(max_cpu_cores=32.0),
        }
        accounts = build_quota_accounts([j1, j2, j3], quotas=quotas)

        assert accounts["t1"].usage.cpu_cores == 12.0
        assert accounts["t1"].usage.memory_mb == 24576.0
        assert accounts["t1"].usage.concurrent_jobs == 2
        assert accounts["t2"].usage.cpu_cores == 2.0
        assert accounts["t2"].usage.concurrent_jobs == 1

    def test_empty_tenants_get_accounts(self):
        quotas = {"t1": ResourceQuotaLimit(max_cpu_cores=64.0)}
        accounts = build_quota_accounts([], quotas=quotas)
        assert "t1" in accounts
        assert accounts["t1"].usage.cpu_cores == 0.0


class TestLoadResourceQuotas:
    def test_policy_store_failure_raises_instead_of_failing_open(self, monkeypatch) -> None:
        def _broken_policy_store():
            raise RuntimeError("store unavailable")

        monkeypatch.setattr("backend.kernel.policy.policy_store.get_policy_store", _broken_policy_store)

        with pytest.raises(RuntimeError, match="ZEN-SCHED-RESOURCE-QUOTA-LOAD-FAILED"):
            load_resource_quotas()
