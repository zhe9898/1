"""Tests for scheduling policy store 鈥?versioned, auditable policy configuration.

Covers:
- Policy data structures (frozen dataclass defaults and immutability)
- PolicyStore lifecycle (apply / rollback / freeze / unfreeze)
- Validation (range checks, constraint violations)
- Diff generation (field-level change tracking)
- YAML bootstrap (load_from_yaml with and without config)
- Integration with scoring engine (job_scoring reads from store)
- Integration with resilience subsystems (backoff, admission, preemption)
"""

from __future__ import annotations

import datetime
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from backend.kernel.policy.policy_store import (
    AdmissionPolicy,
    BackoffPolicy,
    NodeFreshnessPolicy,
    PolicyStore,
    PreemptionPolicy,
    ResourceReservationConfig,
    RetryPolicy,
    SchedulingPolicy,
    ScoringWeights,
    ServiceClassDef,
    _diff_policies,
    validate_policy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _make_node_snapshot(**overrides):
    from backend.kernel.scheduling.job_scheduler import SchedulerNodeSnapshot

    defaults = dict(
        node_id="node-1",
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
    defaults.update(overrides)
    return SchedulerNodeSnapshot(**defaults)


def _make_job(**overrides):
    job = MagicMock()
    job.job_id = overrides.get("job_id", "job-1")
    job.priority = overrides.get("priority", 50)
    job.created_at = overrides.get("created_at", _utcnow())
    job.target_zone = overrides.get("target_zone", "")
    job.target_executor = overrides.get("target_executor", "")
    job.scheduling_strategy = overrides.get("scheduling_strategy", "spread")
    job.required_cpu_cores = overrides.get("required_cpu_cores", 0)
    job.required_memory_mb = overrides.get("required_memory_mb", 0)
    job.required_gpu_vram_mb = overrides.get("required_gpu_vram_mb", 0)
    job.required_storage_mb = overrides.get("required_storage_mb", 0)
    job.data_locality_key = overrides.get("data_locality_key", None)
    job.power_budget_watts = overrides.get("power_budget_watts", None)
    job.thermal_sensitivity = overrides.get("thermal_sensitivity", None)
    job.affinity_labels = overrides.get("affinity_labels", None)
    job.anti_affinity_labels = overrides.get("anti_affinity_labels", None)
    job.batch_key = overrides.get("batch_key", None)
    job.sla_deadline = overrides.get("sla_deadline", None)
    job.sla_seconds = overrides.get("sla_seconds", None)
    job.status = overrides.get("status", "pending")
    job.started_at = overrides.get("started_at", None)
    job.estimated_duration_s = overrides.get("estimated_duration_s", None)
    job.retry_count = overrides.get("retry_count", 0)
    job.max_retries = overrides.get("max_retries", 3)
    job.attempt_count = overrides.get("attempt_count", 0)
    return job


@pytest.fixture(autouse=True)
def _reset_policy_store():
    """Reset the global policy store singleton between tests."""
    import backend.kernel.policy.policy_store as mod

    mod._store = None
    yield
    mod._store = None


# =====================================================================
# Frozen dataclass defaults
# =====================================================================


class TestScoringWeightsDefaults:
    def test_default_values(self):
        sw = ScoringWeights()
        assert sw.priority_max == 160
        assert sw.age_max == 60
        assert sw.age_half_life_seconds == 1800
        assert sw.scarcity_max == 100
        assert sw.reliability_max == 20
        assert sw.strategy_max == 100
        assert sw.zone_match_bonus == 10
        assert sw.load_penalty_max == 40
        assert sw.failure_penalty == 40
        assert sw.anti_affinity_penalty == 50

    def test_frozen(self):
        sw = ScoringWeights()
        with pytest.raises(AttributeError):
            sw.priority_max = 999


class TestRetryPolicyDefaults:
    def test_defaults(self):
        rp = RetryPolicy()
        assert rp.base_delay_seconds == 10
        assert rp.max_delay_seconds == 600
        assert rp.resource_exhausted_multiplier == 3

    def test_frozen(self):
        rp = RetryPolicy()
        with pytest.raises(AttributeError):
            rp.base_delay_seconds = 99


class TestNodeFreshnessDefaults:
    def test_defaults(self):
        fp = NodeFreshnessPolicy()
        assert fp.grace_period_seconds == 10
        assert fp.stale_after_seconds == 45


class TestAdmissionPolicyDefaults:
    def test_defaults(self):
        ap = AdmissionPolicy()
        assert ap.max_pending_per_tenant == 1000
        assert ap.max_total_active == 10_000


class TestPreemptionPolicyDefaults:
    def test_defaults(self):
        pp = PreemptionPolicy()
        assert pp.max_per_window == 5
        assert pp.window_seconds == 300


class TestBackoffPolicyDefaults:
    def test_defaults(self):
        bp = BackoffPolicy()
        assert bp.base_delay_seconds == 5.0
        assert bp.max_delay_seconds == 300.0
        assert bp.max_attempts == 50


class TestSchedulingPolicyDefaults:
    def test_composite_defaults(self):
        policy = SchedulingPolicy()
        assert isinstance(policy.scoring, ScoringWeights)
        assert isinstance(policy.retry, RetryPolicy)
        assert policy.default_strategy == "spread"
        assert "premium" in policy.service_classes
        assert policy.service_classes["premium"].weight == 4.0

    def test_frozen(self):
        policy = SchedulingPolicy()
        with pytest.raises(AttributeError):
            policy.default_strategy = "binpack"


# =====================================================================
# Validation
# =====================================================================


class TestValidation:
    def test_valid_default_policy(self):
        errors = validate_policy(SchedulingPolicy())
        assert errors == []

    def test_priority_max_out_of_range(self):
        policy = SchedulingPolicy(scoring=ScoringWeights(priority_max=999))
        errors = validate_policy(policy)
        assert any("priority_max" in e for e in errors)

    def test_age_half_life_too_small(self):
        policy = SchedulingPolicy(scoring=ScoringWeights(age_half_life_seconds=10))
        errors = validate_policy(policy)
        assert any("age_half_life_seconds" in e for e in errors)

    def test_retry_base_too_small(self):
        policy = SchedulingPolicy(retry=RetryPolicy(base_delay_seconds=0))
        errors = validate_policy(policy)
        assert any("base_delay_seconds" in e for e in errors)

    def test_retry_max_less_than_base(self):
        policy = SchedulingPolicy(retry=RetryPolicy(base_delay_seconds=100, max_delay_seconds=50))
        errors = validate_policy(policy)
        assert any("max_delay_seconds" in e for e in errors)

    def test_freshness_stale_less_than_grace(self):
        policy = SchedulingPolicy(
            freshness=NodeFreshnessPolicy(grace_period_seconds=60, stale_after_seconds=30),
        )
        errors = validate_policy(policy)
        assert any("stale_after_seconds" in e for e in errors)

    def test_admission_zero(self):
        policy = SchedulingPolicy(admission=AdmissionPolicy(max_pending_per_tenant=0))
        errors = validate_policy(policy)
        assert any("max_pending_per_tenant" in e for e in errors)

    def test_reserve_pct_out_of_range(self):
        policy = SchedulingPolicy(
            resource_reservation=ResourceReservationConfig(reserve_pct=1.5),
        )
        errors = validate_policy(policy)
        assert any("reserve_pct" in e for e in errors)

    def test_service_class_zero_weight(self):
        policy = SchedulingPolicy(
            service_classes={"bad": ServiceClassDef(weight=0)},
        )
        errors = validate_policy(policy)
        assert any("weight" in e for e in errors)

    def test_solver_dispatch_limits_must_be_positive(self):
        from backend.core.scheduling_policy_types import SolverConfig

        policy = SchedulingPolicy(
            solver=SolverConfig(
                dispatch_time_budget_ms=-1,
                max_jobs_per_dispatch=0,
                max_nodes_per_dispatch=0,
                max_candidate_pairs_per_dispatch=0,
                plan_affinity_bonus=-1,
            )
        )
        errors = validate_policy(policy)
        assert any("dispatch_time_budget_ms" in e for e in errors)
        assert any("max_jobs_per_dispatch" in e for e in errors)
        assert any("max_nodes_per_dispatch" in e for e in errors)
        assert any("max_candidate_pairs_per_dispatch" in e for e in errors)
        assert any("plan_affinity_bonus" in e for e in errors)


# =====================================================================
# Diff generation
# =====================================================================


class TestDiff:
    def test_no_diff_same_policy(self):
        p = SchedulingPolicy()
        diff = _diff_policies(p, p)
        assert diff == {}

    def test_diff_detects_scoring_change(self):
        old = SchedulingPolicy()
        new = SchedulingPolicy(scoring=ScoringWeights(priority_max=200))
        diff = _diff_policies(old, new)
        assert "scoring.priority_max" in diff
        assert diff["scoring.priority_max"]["old"] == 160
        assert diff["scoring.priority_max"]["new"] == 200

    def test_diff_detects_strategy_change(self):
        old = SchedulingPolicy()
        new = SchedulingPolicy(default_strategy="binpack")
        diff = _diff_policies(old, new)
        assert "default_strategy" in diff


# =====================================================================
# PolicyStore lifecycle
# =====================================================================


class TestPolicyStoreApply:
    def test_initial_version_zero(self):
        store = PolicyStore()
        assert store.version == 0
        assert store.active is not None

    def test_apply_increments_version(self):
        store = PolicyStore()
        new = SchedulingPolicy(default_strategy="binpack")
        pv = store.apply(new, operator="admin", reason="test")
        assert store.version == 1
        assert store.active.default_strategy == "binpack"
        assert pv.version == 1
        assert pv.applied_by == "admin"

    def test_apply_records_diff(self):
        store = PolicyStore()
        new = SchedulingPolicy(scoring=ScoringWeights(priority_max=200))
        pv = store.apply(new, operator="admin", reason="bump priority cap")
        assert "scoring.priority_max" in pv.diff_summary

    def test_apply_rejects_invalid_policy(self):
        store = PolicyStore()
        bad = SchedulingPolicy(scoring=ScoringWeights(priority_max=999))
        with pytest.raises(ValueError, match="validation failed"):
            store.apply(bad, operator="admin", reason="bad")
        assert store.version == 0

    def test_apply_when_frozen_raises(self):
        store = PolicyStore()
        store.freeze()
        new = SchedulingPolicy(default_strategy="binpack")
        with pytest.raises(ValueError, match="frozen"):
            store.apply(new, operator="admin", reason="nope")


class TestPolicyStoreRollback:
    def test_rollback_to_v0(self):
        store = PolicyStore()
        store.apply(
            SchedulingPolicy(default_strategy="binpack"),
            operator="admin",
            reason="v1",
        )
        assert store.version == 1
        store.rollback(0, operator="admin")
        assert store.version == 2
        assert store.active.default_strategy == "spread"

    def test_rollback_missing_version_raises(self):
        store = PolicyStore()
        with pytest.raises(ValueError, match="not in history"):
            store.rollback(99, operator="admin")

    def test_rollback_when_frozen_raises(self):
        store = PolicyStore()
        store.freeze()
        with pytest.raises(ValueError, match="frozen"):
            store.rollback(0, operator="admin")


class TestPolicyStoreFreeze:
    def test_freeze_unfreeze_cycle(self):
        store = PolicyStore()
        store.freeze(reason="deploy lock")
        assert store.frozen
        assert store.freeze_reason == "deploy lock"
        store.unfreeze(operator="sre")
        assert not store.frozen
        assert store.freeze_reason == ""


class TestPolicyStoreSnapshot:
    def test_snapshot_structure(self):
        store = PolicyStore()
        snap = store.snapshot()
        assert "version" in snap
        assert "frozen" in snap
        assert "active_policy" in snap
        assert "history" in snap
        assert "recent_audit" in snap

    def test_snapshot_after_apply(self):
        store = PolicyStore()
        store.apply(
            SchedulingPolicy(default_strategy="binpack"),
            operator="admin",
            reason="test",
        )
        snap = store.snapshot()
        assert snap["version"] == 1
        assert len(snap["history"]) == 2


class TestPolicyStoreVersionDetail:
    def test_get_version_detail(self):
        store = PolicyStore()
        detail = store.get_version_detail(0)
        assert detail is not None
        assert detail.version == 0

    def test_get_missing_version(self):
        store = PolicyStore()
        assert store.get_version_detail(99) is None


# =====================================================================
# YAML load
# =====================================================================


class TestYAMLLoad:
    def test_load_from_yaml_with_policy_section(self):
        yaml_content = """\
scheduling:
  policy:
    scoring:
      priority_max: 200
      age_max: 80
    retry:
      base_delay_seconds: 15
      max_delay_seconds: 900
    default_strategy: binpack
"""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name

        try:
            store = PolicyStore()
            store.load_from_yaml(path)
            assert store.active.scoring.priority_max == 200
            assert store.active.scoring.age_max == 80
            assert store.active.retry.base_delay_seconds == 15
            assert store.active.default_strategy == "binpack"
            assert store.version == 1
        finally:
            os.unlink(path)

    def test_load_from_yaml_no_policy_section_keeps_defaults(self):
        yaml_content = """\
scheduling:
  aging:
    interval_seconds: 300
"""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name

        try:
            store = PolicyStore()
            store.load_from_yaml(path)
            assert store.active.scoring.priority_max == 160
            assert store.version == 0
        finally:
            os.unlink(path)

    def test_load_from_yaml_missing_file_safe(self):
        store = PolicyStore()
        store.load_from_yaml("/nonexistent/path.yaml")
        assert store.version == 0

    def test_load_rejects_invalid_yaml_policy(self):
        yaml_content = """\
scheduling:
  policy:
    scoring:
      priority_max: 999
"""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(yaml_content)
            f.flush()
            path = f.name

        try:
            store = PolicyStore()
            store.load_from_yaml(path)
            # invalid 鈫?keeps defaults
            assert store.active.scoring.priority_max == 160
            assert store.version == 0
        finally:
            os.unlink(path)


# =====================================================================
# Integration 鈥?scoring engine reads from policy store
# =====================================================================


class TestScoringIntegration:
    def _score_with_policy(self, policy, **job_overrides):
        """Score a job with a specific policy active."""
        import backend.kernel.policy.policy_store as mod

        mod._store = PolicyStore()
        mod._store.apply(policy, operator="test", reason="test")

        from backend.kernel.scheduling.job_scoring import score_job_for_node

        job = _make_job(**job_overrides)
        node = _make_node_snapshot()
        now = _utcnow()
        score, breakdown = score_job_for_node(
            job,
            node,
            now=now,
            total_active_nodes=5,
            eligible_nodes_count=3,
            recent_failed_job_ids=set(),
        )
        return score, breakdown

    def test_default_priority_cap_160(self):
        """Priority capped at default 160."""
        _, bd = self._score_with_policy(SchedulingPolicy(), priority=200)
        assert bd["priority"] <= 160

    def test_custom_priority_cap_200(self):
        """Custom policy allows priority up to 200."""
        policy = SchedulingPolicy(scoring=ScoringWeights(priority_max=200))
        _, bd = self._score_with_policy(policy, priority=200)
        assert bd["priority"] <= 200

    def test_zone_bonus_from_policy(self):
        """Zone bonus reads from policy, not hardcoded 10."""
        import backend.kernel.policy.policy_store as mod

        mod._store = PolicyStore()
        policy = SchedulingPolicy(scoring=ScoringWeights(zone_match_bonus=25))
        mod._store.apply(policy, operator="test", reason="test")

        from backend.kernel.scheduling.job_scoring import score_job_for_node

        job = _make_job(target_zone="zone-a")
        node = _make_node_snapshot(zone="zone-a")
        _, bd = score_job_for_node(
            job,
            node,
            now=_utcnow(),
            total_active_nodes=5,
            eligible_nodes_count=3,
            recent_failed_job_ids=set(),
        )
        # Zone bonus should use the policy value (25) not hardcoded (10)
        assert bd["zone"] >= 20


class TestFreshnessIntegration:
    def test_freshness_penalty_uses_policy(self):
        """Freshness penalty reads grace/stale from policy store."""
        import backend.kernel.policy.policy_store as mod

        mod._store = PolicyStore()
        # Set stale_after to 100 鈫?more lenient
        policy = SchedulingPolicy(
            freshness=NodeFreshnessPolicy(grace_period_seconds=10, stale_after_seconds=100),
        )
        mod._store.apply(policy, operator="test", reason="test")

        from backend.kernel.scheduling.job_scoring import _freshness_penalty

        now = _utcnow()
        node = _make_node_snapshot(last_seen_at=now - datetime.timedelta(seconds=50))
        # With default (stale=45), 50s 鈫?fully stale. With stale=100, partially stale.
        penalty = _freshness_penalty(node, now)
        assert 0 < penalty < 15  # partial, not full


class TestRetryIntegration:
    def test_retry_delay_reads_policy_defaults(self):
        """calculate_retry_delay_seconds() uses policy store defaults."""
        import backend.kernel.policy.policy_store as mod

        mod._store = PolicyStore()
        policy = SchedulingPolicy(
            retry=RetryPolicy(base_delay_seconds=20, max_delay_seconds=300),
        )
        mod._store.apply(policy, operator="test", reason="test")

        from backend.kernel.execution.failure_taxonomy import (
            FailureCategory,
            calculate_retry_delay_seconds,
        )

        delay = calculate_retry_delay_seconds(FailureCategory.TRANSIENT, 0)
        assert delay == 20  # base from policy, not hardcoded 10

    def test_retry_delay_explicit_override_wins(self):
        """Explicit base_delay= kwarg overrides policy store."""
        from backend.kernel.execution.failure_taxonomy import (
            FailureCategory,
            calculate_retry_delay_seconds,
        )

        delay = calculate_retry_delay_seconds(
            FailureCategory.TRANSIENT,
            0,
            base_delay=5,
            max_delay=100,
        )
        assert delay == 5


class TestResilienceIntegration:
    def test_admission_reads_policy(self):
        """AdmissionController._resolve_max_pending reads from policy store."""
        import backend.kernel.policy.policy_store as mod
        from backend.kernel.scheduling.scheduling_resilience import AdmissionController

        mod._store = PolicyStore()
        policy = SchedulingPolicy(
            admission=AdmissionPolicy(max_pending_per_tenant=500),
        )
        mod._store.apply(policy, operator="test", reason="test")

        # Reset class-level override to None so it falls through to policy
        AdmissionController.DEFAULT_MAX_PENDING_PER_TENANT = None
        resolved = AdmissionController._resolve_max_pending()
        assert resolved == 500

    def test_backoff_reads_policy(self):
        """SchedulingBackoff._resolve reads from policy store."""
        import backend.kernel.policy.policy_store as mod
        from backend.kernel.scheduling.scheduling_resilience import SchedulingBackoff

        mod._store = PolicyStore()
        policy = SchedulingPolicy(
            backoff=BackoffPolicy(base_delay_seconds=10.0, max_delay_seconds=120.0, max_attempts=30),
        )
        mod._store.apply(policy, operator="test", reason="test")

        SchedulingBackoff.BASE_DELAY_S = None
        SchedulingBackoff.MAX_DELAY_S = None
        SchedulingBackoff.MAX_ATTEMPTS = None

        base, maxd, maxa = SchedulingBackoff._resolve()
        assert base == 10.0
        assert maxd == 120.0
        assert maxa == 30

    def test_preemption_reads_policy(self):
        """PreemptionBudgetPolicy._resolve_limits reads from policy store."""
        import backend.kernel.policy.policy_store as mod
        from backend.kernel.scheduling.scheduling_resilience import PreemptionBudgetPolicy

        mod._store = PolicyStore()
        policy = SchedulingPolicy(
            preemption=PreemptionPolicy(max_per_window=10, window_seconds=600),
        )
        mod._store.apply(policy, operator="test", reason="test")

        PreemptionBudgetPolicy.max_preemptions_per_window = None
        PreemptionBudgetPolicy.window_seconds = None

        max_pw, window_s = PreemptionBudgetPolicy._resolve_limits()
        assert max_pw == 10
        assert window_s == 600


# =====================================================================
# Governance facade proxy
# =====================================================================


class TestGovernanceFacadeProxy:
    def test_policy_snapshot(self):
        from backend.core.governance_facade import get_governance_facade

        facade = get_governance_facade()
        snap = facade.policy_snapshot()
        assert "version" in snap
        assert "active_policy" in snap

    def test_apply_and_rollback(self):
        from backend.core.governance_facade import get_governance_facade

        facade = get_governance_facade()
        policy = SchedulingPolicy(default_strategy="binpack")
        facade.apply_policy(policy, operator="admin", reason="test")
        assert facade.active_policy.default_strategy == "binpack"

        facade.rollback_policy(0, operator="admin")
        assert facade.active_policy.default_strategy == "spread"

    def test_freeze_unfreeze(self):
        from backend.core.governance_facade import get_governance_facade

        facade = get_governance_facade()
        facade.freeze_policy(reason="deploy")
        # Should raise on apply
        with pytest.raises(ValueError, match="frozen"):
            facade.apply_policy(
                SchedulingPolicy(),
                operator="admin",
                reason="nope",
            )
        facade.unfreeze_policy(operator="sre")
        # Should succeed now
        facade.apply_policy(
            SchedulingPolicy(default_strategy="binpack"),
            operator="sre",
            reason="ok",
        )


# =====================================================================
# Audit log
# =====================================================================


class TestAuditLog:
    def test_apply_creates_audit_entry(self):
        store = PolicyStore()
        store.apply(SchedulingPolicy(), operator="admin", reason="test")
        snap = store.snapshot()
        audit = snap["recent_audit"]
        assert len(audit) >= 1
        assert audit[-1]["action"] == "apply"
        assert audit[-1]["operator"] == "admin"

    def test_freeze_creates_audit_entry(self):
        store = PolicyStore()
        store.freeze(reason="lock")
        snap = store.snapshot()
        audit = snap["recent_audit"]
        assert any(e["action"] == "freeze" for e in audit)

    def test_unfreeze_creates_audit_entry(self):
        store = PolicyStore()
        store.freeze(reason="lock")
        store.unfreeze(operator="sre")
        snap = store.snapshot()
        audit = snap["recent_audit"]
        assert any(e["action"] == "unfreeze" for e in audit)
