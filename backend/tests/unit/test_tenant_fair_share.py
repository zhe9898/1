"""Tests for tenant fair-share scheduling (GlobalFairScheduler + TenantFairShareGate).

Covers:
- Config loading from system.yaml
- Service class weight mapping
- Per-tenant quota enforcement in the scheduling engine
- TTL cache invalidation
- Default fallback behaviour
"""

from __future__ import annotations

import datetime
import textwrap
from pathlib import Path

import pytest

from backend.kernel.scheduling.business_scheduling import (
    SchedulingContext,
    SchedulingEngine,
    apply_business_filters,
)
from backend.kernel.scheduling.queue_stratification import (
    SERVICE_CLASS_CONFIG,
    GlobalFairScheduler,
    get_fair_scheduler,
)
from backend.models.job import Job


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


# 鈹€鈹€ GlobalFairScheduler unit tests 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestGlobalFairScheduler:
    def setup_method(self) -> None:
        # Reset class-level cache before each test
        GlobalFairScheduler._cache = None
        GlobalFairScheduler._cache_ts = 0.0
        GlobalFairScheduler._default_service_class = "standard"

    def test_default_quota_without_config(self) -> None:
        """Tenants not in config get default service class quota."""
        fs = GlobalFairScheduler()
        quota = fs.get_quota("unknown-tenant")
        assert quota.service_class == "standard"
        expected_max = SERVICE_CLASS_CONFIG["standard"]["max_jobs_per_round"]
        assert quota.max_jobs_per_round == expected_max

    def test_load_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Quotas load correctly from system.yaml scheduling section."""
        yaml_content = textwrap.dedent("""\
            scheduling:
              default_service_class: economy
              tenant_quotas:
                tenant-alpha:
                  service_class: premium
                tenant-beta:
                  service_class: economy
                  max_jobs_per_round: 8
        """)
        yaml_file = tmp_path / "system.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        monkeypatch.chdir(tmp_path)

        # Reset policy store singleton so it re-reads from the test's temp system.yaml
        import backend.kernel.policy.policy_store as _sps

        monkeypatch.setattr(_sps, "_store", None)

        fs = GlobalFairScheduler()
        # Force fresh load
        fs.invalidate_cache()

        quota_alpha = fs.get_quota("tenant-alpha")
        assert quota_alpha.service_class == "premium"
        assert quota_alpha.max_jobs_per_round == SERVICE_CLASS_CONFIG["premium"]["max_jobs_per_round"]
        assert quota_alpha.weight == SERVICE_CLASS_CONFIG["premium"]["weight"]

        quota_beta = fs.get_quota("tenant-beta")
        assert quota_beta.service_class == "economy"
        assert quota_beta.max_jobs_per_round == 8  # explicit override

    def test_cache_ttl(self) -> None:
        """After loading, repeated calls use cache until TTL expires."""
        fs = GlobalFairScheduler()
        # First call populates cache
        _ = fs.get_quota("any")
        assert fs._cache is not None
        ts1 = fs._cache_ts
        # Second call uses cache (no re-read)
        _ = fs.get_quota("any")
        assert fs._cache_ts == ts1

    def test_invalidate_cache(self) -> None:
        """invalidate_cache() forces a fresh read on next access."""
        fs = GlobalFairScheduler()
        _ = fs.get_quota("any")
        assert fs._cache is not None
        fs.invalidate_cache()
        assert fs._cache is None

    def test_service_class_config_completeness(self) -> None:
        """All service classes have required fields."""
        for sc, cfg in SERVICE_CLASS_CONFIG.items():
            assert "weight" in cfg, f"{sc} missing weight"
            assert "max_jobs_per_round" in cfg, f"{sc} missing max_jobs_per_round"
            assert isinstance(cfg["weight"], (int, float))
            assert isinstance(cfg["max_jobs_per_round"], int)

    def test_premium_has_highest_weight(self) -> None:
        """Premium service class has the highest weight."""
        weights = {sc: cfg["weight"] for sc, cfg in SERVICE_CLASS_CONFIG.items()}
        assert weights["premium"] == max(weights.values())

    def test_apply_fair_share_enforces_quota(self) -> None:
        """apply_fair_share() respects per-tenant max_jobs_per_round."""
        fs = GlobalFairScheduler()
        # Default standard quota = 20, so 25 jobs from same tenant 鈫?capped at 20
        jobs = [_job(job_id=f"j-{i}", tenant_id="t1") for i in range(25)]
        filtered = fs.apply_fair_share(jobs)
        quota = fs.get_quota("t1")
        assert len(filtered) == quota.max_jobs_per_round

    def test_apply_fair_share_multi_tenant(self) -> None:
        """Multiple tenants each get their own quota allocation."""
        fs = GlobalFairScheduler()
        jobs = [_job(job_id=f"t1-{i}", tenant_id="t1") for i in range(10)] + [_job(job_id=f"t2-{i}", tenant_id="t2") for i in range(10)]
        filtered = fs.apply_fair_share(jobs)
        t1_count = sum(1 for j in filtered if j.tenant_id == "t1")
        t2_count = sum(1 for j in filtered if j.tenant_id == "t2")
        assert t1_count == 10
        assert t2_count == 10

    def test_get_all_quotas(self) -> None:
        """get_all_quotas returns a dict (possibly empty)."""
        fs = GlobalFairScheduler()
        quotas = fs.get_all_quotas()
        assert isinstance(quotas, dict)

    def test_singleton(self) -> None:
        """get_fair_scheduler returns the same instance."""
        a = get_fair_scheduler()
        b = get_fair_scheduler()
        assert a is b


# 鈹€鈹€ TenantFairShareGate tests 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


class TestTenantFairShareGate:
    def setup_method(self) -> None:
        GlobalFairScheduler._cache = None
        GlobalFairScheduler._cache_ts = 0.0
        GlobalFairScheduler._default_service_class = "standard"

    def test_gate_passes_within_quota(self) -> None:
        """Jobs within tenant quota pass the gate."""
        now = _utcnow()
        jobs = [_job(job_id=f"j-{i}", tenant_id="default") for i in range(5)]
        result = apply_business_filters(
            jobs,
            completed_job_ids=set(),
            available_slots=100,
            parent_jobs={},
            now=now,
        )
        assert len(result) == 5

    def test_gate_cuts_at_quota(self) -> None:
        """Jobs beyond per-tenant quota are dropped by the gate."""
        now = _utcnow()
        # Create more jobs than the standard quota allows
        quota = SERVICE_CLASS_CONFIG["standard"]["max_jobs_per_round"]
        jobs = [_job(job_id=f"j-{i}", tenant_id="default") for i in range(int(quota) + 10)]
        result = apply_business_filters(
            jobs,
            completed_job_ids=set(),
            available_slots=100,
            parent_jobs={},
            now=now,
        )
        assert len(result) == quota

    def test_gate_independent_per_tenant(self) -> None:
        """Fair-share quota is tracked per-tenant independently."""
        now = _utcnow()
        quota = int(SERVICE_CLASS_CONFIG["standard"]["max_jobs_per_round"])
        jobs = [_job(job_id=f"t1-{i}", tenant_id="t1") for i in range(quota + 5)] + [_job(job_id=f"t2-{i}", tenant_id="t2") for i in range(3)]
        result = apply_business_filters(
            jobs,
            completed_job_ids=set(),
            available_slots=100,
            parent_jobs={},
            now=now,
        )
        t1_ids = [j.job_id for j in result if j.tenant_id == "t1"]
        t2_ids = [j.job_id for j in result if j.tenant_id == "t2"]
        assert len(t1_ids) == quota
        assert len(t2_ids) == 3

    def test_gate_stats_recorded(self) -> None:
        """Engine records drop stats for fair-share gate."""
        now = _utcnow()
        quota = int(SERVICE_CLASS_CONFIG["standard"]["max_jobs_per_round"])
        jobs = [_job(job_id=f"j-{i}", tenant_id="default") for i in range(quota + 3)]
        engine = SchedulingEngine()
        ctx = SchedulingContext(
            now=now,
            completed_job_ids=set(),
            available_slots=100,
            parent_jobs={},
        )
        result = engine.run(jobs, ctx)
        assert len(result) == quota
        # 3 jobs dropped by tenant_fair_share gate
        assert ctx.stats.get("tenant_fair_share", 0) == 3

    def test_data_dict_available(self) -> None:
        """SchedulingContext.data is available for gate extensions."""
        ctx = SchedulingContext(
            now=_utcnow(),
            completed_job_ids=set(),
            available_slots=10,
            parent_jobs={},
        )
        assert isinstance(ctx.data, dict)
        assert len(ctx.data) == 0
