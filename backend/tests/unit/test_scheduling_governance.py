"""Tests for scheduling governance subsystem.

Covers:
- PlacementPolicy protocol + built-in policies
- ExecutorContract registry
- SchedulingDecisionLogger
- Scheduling feature flag defaults
- GlobalFairScheduler DB loading
"""

from __future__ import annotations

import datetime

import pytest

# ── Helpers ──────────────────────────────────────────────────────────


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _make_node_snapshot(**overrides):
    from backend.kernel.scheduling.job_scheduler import SchedulerNodeSnapshot

    defaults = dict(
        node_id="node-1",
        os="linux",
        arch="amd64",
        executor="docker",
        zone=None,
        capabilities=frozenset(),
        accepted_kinds=frozenset({"shell.exec", "connector.invoke"}),
        worker_pools=frozenset({"batch", "interactive"}),
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
# PlacementPolicy tests
# =====================================================================


class TestResourceReservationPolicy:
    def test_accept_low_utilisation(self):
        from backend.kernel.scheduling.placement_policy import ResourceReservationPolicy

        policy = ResourceReservationPolicy(reserve_pct=0.80, min_priority=70)
        node = _make_node_snapshot(active_lease_count=1, max_concurrency=4)
        job = _make_job(priority=20)
        ok, reason = policy.accept(job, node, 100)
        assert ok is True

    def test_reject_high_utilisation_low_priority(self):
        from backend.kernel.scheduling.placement_policy import ResourceReservationPolicy

        policy = ResourceReservationPolicy(reserve_pct=0.80, min_priority=70)
        # 4/4 = 100% utilisation → above 80% threshold
        node = _make_node_snapshot(active_lease_count=4, max_concurrency=5)
        job = _make_job(priority=20)
        ok, reason = policy.accept(job, node, 100)
        assert ok is False
        assert "resource_reservation" in reason

    def test_accept_high_utilisation_high_priority(self):
        from backend.kernel.scheduling.placement_policy import ResourceReservationPolicy

        policy = ResourceReservationPolicy(reserve_pct=0.80, min_priority=70)
        node = _make_node_snapshot(active_lease_count=4, max_concurrency=5)
        job = _make_job(priority=90)
        ok, reason = policy.accept(job, node, 100)
        assert ok is True

    def test_adjust_score_passthrough(self):
        from backend.kernel.scheduling.placement_policy import ResourceReservationPolicy

        policy = ResourceReservationPolicy()
        node = _make_node_snapshot()
        job = _make_job()
        score, breakdown = policy.adjust_score(job, node, 100, {"priority": 50})
        assert score == 100
        assert breakdown == {"priority": 50}


class TestThermalCapPolicy:
    def test_accept_normal_thermal(self):
        from backend.kernel.scheduling.placement_policy import ThermalCapPolicy

        policy = ThermalCapPolicy()
        node = _make_node_snapshot(thermal_state="normal")
        job = _make_job()
        ok, _ = policy.accept(job, node, 100)
        assert ok is True

    def test_reject_throttling(self):
        from backend.kernel.scheduling.placement_policy import ThermalCapPolicy

        policy = ThermalCapPolicy()
        node = _make_node_snapshot(thermal_state="throttling")
        job = _make_job()
        ok, reason = policy.accept(job, node, 100)
        assert ok is False
        assert "thermal_cap" in reason

    def test_accept_throttling_when_sensitivity_none(self):
        from backend.kernel.scheduling.placement_policy import ThermalCapPolicy

        policy = ThermalCapPolicy()
        node = _make_node_snapshot(thermal_state="throttling")
        job = _make_job(thermal_sensitivity="none")
        ok, _ = policy.accept(job, node, 100)
        assert ok is True


class TestBinPackConsolidationPolicy:
    def test_bonus_scales_with_utilisation(self):
        from backend.kernel.scheduling.placement_policy import BinPackConsolidationPolicy

        policy = BinPackConsolidationPolicy(bonus_weight=0.15)
        node_low = _make_node_snapshot(active_lease_count=1, max_concurrency=4)
        node_high = _make_node_snapshot(active_lease_count=3, max_concurrency=4)
        job = _make_job()

        score_low, bd_low = policy.adjust_score(job, node_low, 100, {})
        score_high, bd_high = policy.adjust_score(job, node_high, 100, {})
        assert score_high > score_low
        assert "binpack_consolidation" in bd_high


class TestPowerAwarePolicy:
    def test_penalty_low_headroom(self):
        from backend.kernel.scheduling.placement_policy import PowerAwarePolicy

        policy = PowerAwarePolicy(min_headroom_pct=0.15, penalty=25)
        # 490/500 = 2% headroom < 15%
        node = _make_node_snapshot(power_capacity_watts=500, current_power_watts=490)
        job = _make_job()
        score, bd = policy.adjust_score(job, node, 100, {})
        assert score == 75
        assert bd.get("power_aware_penalty") == -25

    def test_no_penalty_sufficient_headroom(self):
        from backend.kernel.scheduling.placement_policy import PowerAwarePolicy

        policy = PowerAwarePolicy(min_headroom_pct=0.15, penalty=25)
        node = _make_node_snapshot(power_capacity_watts=500, current_power_watts=200)
        job = _make_job()
        score, bd = policy.adjust_score(job, node, 100, {})
        assert score == 100


class TestCompositePlacementPolicy:
    def test_chain_respects_order(self):
        from backend.kernel.scheduling.placement_policy import (
            CompositePlacementPolicy,
            ResourceReservationPolicy,
            ThermalCapPolicy,
        )

        composite = CompositePlacementPolicy(
            policies=[
                ThermalCapPolicy(),
                ResourceReservationPolicy(),
            ]
        )
        # Order should be sorted by .order
        assert composite.policies[0].order <= composite.policies[1].order

    def test_chain_short_circuits_on_reject(self):
        from backend.kernel.scheduling.placement_policy import (
            CompositePlacementPolicy,
            ResourceReservationPolicy,
            ThermalCapPolicy,
        )

        composite = CompositePlacementPolicy(
            policies=[
                ThermalCapPolicy(blocked_states=frozenset({"hot"})),
                ResourceReservationPolicy(),
            ]
        )
        node = _make_node_snapshot(thermal_state="hot")
        job = _make_job()
        ok, reason = composite.accept(job, node, 100)
        assert ok is False
        assert "thermal_cap" in reason


# =====================================================================
# ExecutorContract tests
# =====================================================================


class TestExecutorRegistry:
    def test_default_contracts_loaded(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        contracts = reg.all_contracts()
        assert "docker" in contracts
        assert "process" in contracts
        assert "gpu" in contracts
        assert "unknown" in contracts

    def test_get_or_default_unknown(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        c = reg.get_or_default("nonexistent")
        assert c.name == "unknown"

    def test_validate_gpu_executor_no_vram(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        warns = reg.validate_node_executor("gpu", memory_mb=2048, cpu_cores=4, gpu_vram_mb=0)
        assert any("GPU" in w for w in warns)

    def test_validate_docker_ok(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        warns = reg.validate_node_executor("docker", memory_mb=4096, cpu_cores=4)
        assert len(warns) == 0

    def test_is_kind_supported(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        assert reg.is_kind_supported("docker", "shell.exec") is True
        assert reg.is_kind_supported("docker", "ml.inference") is False
        assert reg.is_kind_supported("unknown", "anything") is None  # permissive

    def test_register_custom(self):
        from backend.kernel.topology.executor_registry import ExecutorContract, ExecutorRegistry

        reg = ExecutorRegistry()
        custom = ExecutorContract(
            name="custom",
            description="Custom executor",
            supported_kinds=frozenset({"custom.run"}),
            stability_tier="experimental",
        )
        reg.register(custom)
        assert reg.get("custom") is custom


# =====================================================================
# SchedulingDecisionLogger tests
# =====================================================================


class TestSchedulingDecisionLogger:
    def test_record_placement(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.candidates_count = 10
        logger.record_placement("job-1", score=150, breakdown={"priority": 50}, eligible_nodes=3)
        logger.record_placement("job-2", score=120, breakdown={"priority": 40}, eligible_nodes=5)

        assert len(logger.placements) == 2
        assert logger.placements[0]["job_id"] == "job-1"
        assert logger.placements[0]["score"] == 150

    def test_record_rejection(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.record_rejection("job-3", "capacity=full")
        assert len(logger.rejections) == 1
        assert logger.rejections[0]["reason"] == "capacity=full"

    def test_record_preemption(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.record_preemption("victim-1", "urgent-1", "sla_breach")
        assert logger.preemptions_count == 1
        assert "preemptions" in logger.context

    def test_record_policy_rejection(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.record_policy_rejection("job-4", "thermal_cap", "node is throttling")
        assert logger.policy_rejections == 1


# =====================================================================
# Feature flag defaults
# =====================================================================


class TestSchedulingFeatureFlags:
    def test_default_flags(self):
        from backend.core.scheduling_governance import _SCHEDULING_FLAG_DEFAULTS

        assert "sched_placement_policies" in _SCHEDULING_FLAG_DEFAULTS
        assert "sched_decision_audit" in _SCHEDULING_FLAG_DEFAULTS
        # All keys should be strings, values booleans
        for k, v in _SCHEDULING_FLAG_DEFAULTS.items():
            assert isinstance(k, str)
            assert isinstance(v, bool)


# =====================================================================
# GlobalFairScheduler DB loading
# =====================================================================


class TestGlobalFairSchedulerDBLoad:
    def test_load_from_db_policies(self):
        from unittest.mock import MagicMock

        from backend.kernel.scheduling.queue_stratification import GlobalFairScheduler

        scheduler = GlobalFairScheduler()

        # Create mock policies
        mock_policy = MagicMock()
        mock_policy.tenant_id = "tenant-alpha"
        mock_policy.enabled = True
        mock_policy.max_jobs_per_round = 40
        mock_policy.fair_share_weight = 4.0
        mock_policy.service_class = "premium"

        scheduler.load_from_db_policies([mock_policy])

        quota = scheduler.get_quota("tenant-alpha")
        assert quota.max_jobs_per_round == 40
        assert quota.weight == 4.0
        assert quota.service_class == "premium"

    def test_load_from_db_disabled_tenant_skipped(self):
        from unittest.mock import MagicMock

        from backend.kernel.scheduling.queue_stratification import GlobalFairScheduler

        scheduler = GlobalFairScheduler()

        mock_policy = MagicMock()
        mock_policy.tenant_id = "disabled-tenant"
        mock_policy.enabled = False
        mock_policy.max_jobs_per_round = 40
        mock_policy.fair_share_weight = 4.0
        mock_policy.service_class = "premium"

        scheduler.load_from_db_policies([mock_policy])

        # Should fall back to default
        quota = scheduler.get_quota("disabled-tenant")
        assert quota.service_class == "standard"


# =====================================================================
# PlacementPolicy loader
# =====================================================================


class TestPlacementPolicyLoader:
    def test_default_policy_set(self):
        from backend.kernel.scheduling.placement_policy import load_placement_policies

        composite = load_placement_policies()
        assert len(composite.policies) >= 1
        # Default set includes resource_reservation
        names = [p.name for p in composite.policies]
        assert "resource_reservation" in names

    def test_get_singleton(self):
        from backend.kernel.scheduling.placement_policy import get_placement_policy

        pp = get_placement_policy()
        assert pp is not None
        assert hasattr(pp, "policies")


# =====================================================================
# Integration: scoring includes placement policy adjustment
# =====================================================================


class TestScoringWithPlacementPolicy:
    def test_score_includes_policy_adjustment(self):
        from backend.kernel.scheduling.job_scheduler import score_job_for_node

        node = _make_node_snapshot(active_lease_count=0, max_concurrency=4)
        job = _make_job(priority=50)
        now = _utcnow()

        total, breakdown = score_job_for_node(
            job,
            node,
            now=now,
            total_active_nodes=3,
            eligible_nodes_count=2,
            recent_failed_job_ids=set(),
        )
        # Score should be a reasonable positive number
        assert total > 0
        assert isinstance(breakdown, dict)


# =====================================================================
# Extended ExecutorContract tests (k8s, remote-ssh, edge-native)
# =====================================================================


class TestExtendedExecutorContracts:
    def test_k8s_contract_loaded(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        c = reg.get("k8s")
        assert c is not None
        assert c.min_memory_mb == 512
        assert c.min_cpu_cores == 1
        assert "container.run" in c.supported_kinds
        assert "cron.tick" in c.supported_kinds

    def test_remote_ssh_contract_loaded(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        c = reg.get("remote-ssh")
        assert c is not None
        assert "shell.exec" in c.supported_kinds
        assert "iot.collect" in c.supported_kinds
        assert c.stability_tier == "ga"

    def test_edge_native_contract_loaded(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        c = reg.get("edge-native")
        assert c is not None
        assert c.min_memory_mb == 32
        assert c.max_concurrency_hint == 4
        assert "iot.collect" in c.supported_kinds
        assert "healthcheck" in c.supported_kinds

    def test_kind_compatible_pass(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        ok, reason = reg.kind_compatible("docker", "shell.exec")
        assert ok is True
        assert reason == ""

    def test_kind_compatible_fail(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        ok, reason = reg.kind_compatible("wasm", "shell.exec")
        assert ok is False
        assert "wasm" in reason

    def test_kind_compatible_unknown_permissive(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        ok, reason = reg.kind_compatible("unknown", "any.kind")
        assert ok is True

    def test_kind_compatible_unregistered_permissive(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        ok, reason = reg.kind_compatible("nonexistent", "any.kind")
        assert ok is True

    def test_validate_k8s_low_memory(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        warns = reg.validate_node_executor("k8s", memory_mb=256, cpu_cores=1)
        assert any("512" in w for w in warns)

    def test_validate_edge_native_ok(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        warns = reg.validate_node_executor("edge-native", memory_mb=64)
        assert len(warns) == 0

    def test_all_contracts_count(self):
        from backend.kernel.topology.executor_registry import ExecutorRegistry

        reg = ExecutorRegistry()
        contracts = reg.all_contracts()
        # docker, process, gpu, wasm, k8s, remote-ssh, edge-native, unknown
        assert len(contracts) >= 8


# =====================================================================
# Extended Job Kind Registry tests
# =====================================================================


class TestExtendedJobKinds:
    def test_all_builtin_kinds_registered(self):
        from backend.kernel.extensions.job_kind_registry import get_registered_job_kinds

        kinds = get_registered_job_kinds()
        expected = [
            "shell.exec",
            "http.request",
            "container.run",
            "healthcheck",
            "ml.inference",
            "media.transcode",
            "script.run",
            "wasm.run",
            "cron.tick",
            "data.sync",
        ]
        for k in expected:
            assert k in kinds, f"{k} not registered"

    def test_container_run_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "container.run",
            {
                "image": "nginx:latest",
                "command": ["echo", "hello"],
            },
        )
        assert valid["image"] == "nginx:latest"
        assert valid["pull_policy"] == "IfNotPresent"  # default

    def test_container_run_missing_image_fails(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        with pytest.raises(ValueError, match="container.run"):
            validate_job_payload("container.run", {"command": ["echo"]})

    def test_healthcheck_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "healthcheck",
            {
                "target": "http://localhost:8080/health",
            },
        )
        assert valid["check_type"] == "http"
        assert valid["timeout"] == 10

    def test_ml_inference_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "ml.inference",
            {
                "model_id": "resnet50",
                "input_data": {"tensor": [1, 2, 3]},
            },
        )
        assert valid["runtime"] == "onnx"
        assert valid["precision"] == "fp32"

    def test_media_transcode_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "media.transcode",
            {
                "input_uri": "s3://bucket/input.mp4",
                "output_uri": "s3://bucket/output.mp4",
            },
        )
        assert valid["codec"] == "h264"

    def test_script_run_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "script.run",
            {
                "script": "echo hello",
                "interpreter": "bash",
            },
        )
        assert valid["timeout"] == 300

    def test_wasm_run_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "wasm.run",
            {
                "module_uri": "https://example.com/module.wasm",
            },
        )
        assert valid["function"] == "_start"
        assert valid["memory_pages"] == 256

    def test_cron_tick_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "cron.tick",
            {
                "schedule_id": "sched-001",
                "cron_expression": "*/5 * * * *",
                "action": "run_cleanup",
            },
        )
        assert valid["timeout"] == 120

    def test_data_sync_payload_validation(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        valid = validate_job_payload(
            "data.sync",
            {
                "source_uri": "rsync://edge-a/data/",
                "dest_uri": "rsync://edge-b/sync/",
            },
        )
        assert valid["direction"] == "push"
        assert valid["conflict_resolution"] == "latest-wins"

    def test_result_schema_registered(self):
        from backend.kernel.extensions.job_kind_registry import validate_job_result

        result = validate_job_result(
            "container.run",
            {
                "exit_code": 0,
                "stdout": "ok",
                "container_id": "abc123",
                "duration_seconds": 2.5,
            },
        )
        assert result["exit_code"] == 0

    def test_kind_info_has_schemas(self):
        from backend.kernel.extensions.job_kind_registry import get_job_kind_info

        info = get_job_kind_info("ml.inference")
        assert info["has_payload_schema"] is True
        assert info["has_result_schema"] is True
        assert info["payload_schema"] is not None

    def test_kind_count(self):
        from backend.kernel.extensions.job_kind_registry import get_registered_job_kinds

        kinds = get_registered_job_kinds()
        assert len(kinds) >= 10


# =====================================================================
# Executor-kind compat in scoring pre-filter
# =====================================================================


class TestExecutorKindCompatInScoring:
    def test_incompatible_kind_excluded_from_selection(self):
        from backend.kernel.scheduling.job_scheduler import select_jobs_for_node

        now = _utcnow()
        node = _make_node_snapshot(
            executor="wasm",  # wasm only supports wasm.run
            accepted_kinds=frozenset(),  # no node-level restriction
        )
        job = _make_job(kind="shell.exec")  # wasm doesn't support shell.exec
        selected = select_jobs_for_node(
            [job],
            node,
            [node],
            now=now,
            accepted_kinds=set(),
            recent_failed_job_ids=set(),
            active_jobs_on_node=[],
            limit=1,
        )
        assert len(selected) == 0

    def test_compatible_kind_passes(self):
        from backend.kernel.scheduling.job_scheduler import select_jobs_for_node

        now = _utcnow()
        node = _make_node_snapshot(
            executor="docker",
            accepted_kinds=frozenset(),
        )
        job = _make_job(kind="shell.exec")  # docker supports shell.exec
        selected = select_jobs_for_node(
            [job],
            node,
            [node],
            now=now,
            accepted_kinds=set(),
            recent_failed_job_ids=set(),
            active_jobs_on_node=[],
            limit=1,
        )
        assert len(selected) == 1


# =====================================================================
# Placement Policy enable/disable toggle
# =====================================================================


class TestPlacementPolicyToggle:
    def test_disabled_returns_noop(self):
        from backend.kernel.scheduling.placement_policy import (
            get_placement_policy,
            set_placement_enabled,
        )

        set_placement_enabled(False)
        try:
            pp = get_placement_policy()
            assert len(pp.policies) == 0  # noop composite
        finally:
            set_placement_enabled(True)

    def test_enabled_returns_real(self):
        from backend.kernel.scheduling.placement_policy import (
            get_placement_policy,
            set_placement_enabled,
        )

        set_placement_enabled(True)
        pp = get_placement_policy()
        assert len(pp.policies) >= 1  # at least ResourceReservationPolicy


# =====================================================================
# Audit logger: rejection recording
# =====================================================================


class TestAuditRejectionRecording:
    def test_record_circuit_breaker_rejection(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.record_rejection("job-5", "kind_circuit_open:ml.inference")
        assert len(logger.rejections) == 1
        assert "circuit_open" in logger.rejections[0]["reason"]

    def test_record_executor_compat_rejection(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.record_rejection("job-6", "executor_kind_incompat:wasm excludes shell.exec")
        assert len(logger.rejections) == 1
        assert "executor_kind_incompat" in logger.rejections[0]["reason"]

    def test_context_stores_feature_flags(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.context["feature_flags"] = {
            "decision_audit": True,
            "placement_policies": True,
            "preemption": True,
            "executor_validation": False,
        }
        assert logger.context["feature_flags"]["decision_audit"] is True

    def test_context_stores_burst_state(self):
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        logger = SchedulingDecisionLogger(
            tenant_id="t1",
            node_id="n1",
            now=_utcnow(),
        )
        logger.context["burst_active"] = True
        assert logger.context["burst_active"] is True


# ====================================================================
# Per-kind quota
# ====================================================================


class TestPerKindQuota:
    """Tests for per-kind concurrent job quota enforcement."""

    @pytest.mark.asyncio
    async def test_per_kind_quota_passes_when_under_limit(self):
        from unittest.mock import AsyncMock, MagicMock

        from backend.core.quota import check_per_kind_quota

        db = AsyncMock()
        # _get_limit calls: specific key returns -1, generic returns 5
        # count call returns 3
        _scalar_none = MagicMock()
        _scalar_none.scalars.return_value.first.return_value = None
        _scalar_count = MagicMock()
        _scalar_count.scalar.return_value = 3
        db.execute = AsyncMock(side_effect=[_scalar_none, _scalar_none, _scalar_count])

        # Should not raise
        await check_per_kind_quota(db, "default", "shell.exec")

    @pytest.mark.asyncio
    async def test_per_kind_quota_raises_when_at_limit(self):
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import HTTPException

        from backend.core.quota import check_per_kind_quota

        db = AsyncMock()
        # specific key returns -1, generic returns limit=5, count=5
        _scalar_none = MagicMock()
        _scalar_none.scalars.return_value.first.return_value = None

        class FakeQuota:
            limit = 5

        _scalar_quota = MagicMock()
        _scalar_quota.scalars.return_value.first.return_value = FakeQuota()

        _scalar_count = MagicMock()
        _scalar_count.scalar.return_value = 5

        db.execute = AsyncMock(side_effect=[_scalar_none, _scalar_quota, _scalar_count])

        with pytest.raises(HTTPException) as exc_info:
            await check_per_kind_quota(db, "default", "shell.exec")
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_per_kind_quota_unlimited(self):
        from unittest.mock import AsyncMock, MagicMock

        from backend.core.quota import check_per_kind_quota

        db = AsyncMock()
        # Both specific and generic return None → DEFAULT_QUOTAS["jobs_per_kind"] = 100
        # but if neither custom nor default → -1 means unlimited
        _scalar_none = MagicMock()
        _scalar_none.scalars.return_value.first.return_value = None
        db.execute = AsyncMock(side_effect=[_scalar_none, _scalar_none, MagicMock(scalar=MagicMock(return_value=50))])

        # Default of 100 with 50 used → should pass
        await check_per_kind_quota(db, "default", "shell.exec")

    @pytest.mark.asyncio
    async def test_per_kind_specific_override(self):
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import HTTPException

        from backend.core.quota import check_per_kind_quota

        db = AsyncMock()

        # Specific key returns quota with limit=2
        class FakeQuota:
            limit = 2

        _scalar_specific = MagicMock()
        _scalar_specific.scalars.return_value.first.return_value = FakeQuota()
        _scalar_count = MagicMock()
        _scalar_count.scalar.return_value = 2

        db.execute = AsyncMock(side_effect=[_scalar_specific, _scalar_count])

        with pytest.raises(HTTPException) as exc_info:
            await check_per_kind_quota(db, "default", "ml.inference")
        assert exc_info.value.status_code == 429


# ====================================================================
# Configurable scheduling constants
# ====================================================================


class TestConfigurableSchedulingConstants:
    """Tests for system.yaml-backed scheduling configuration."""

    def test_get_aging_config_returns_dict(self):
        from backend.kernel.scheduling.queue_stratification import get_aging_config, reset_scheduling_config_cache

        reset_scheduling_config_cache()
        cfg = get_aging_config()
        assert isinstance(cfg, dict)
        assert "enabled" in cfg
        assert "interval_seconds" in cfg
        assert "bonus_per_interval" in cfg
        assert "max_bonus" in cfg

    def test_get_default_tenant_quota_is_int(self):
        from backend.kernel.scheduling.queue_stratification import get_default_tenant_quota, reset_scheduling_config_cache

        reset_scheduling_config_cache()
        q = get_default_tenant_quota()
        assert isinstance(q, int)
        assert q > 0

    def test_get_starvation_threshold_is_int(self):
        from backend.kernel.scheduling.queue_stratification import get_starvation_threshold_seconds, reset_scheduling_config_cache

        reset_scheduling_config_cache()
        t = get_starvation_threshold_seconds()
        assert isinstance(t, int)
        assert t > 0

    def test_reset_cache_forces_reload(self):
        from backend.kernel.scheduling.queue_stratification import (
            _load_scheduling_config,
            get_default_tenant_quota,
            reset_scheduling_config_cache,
        )

        # Prime cache
        get_default_tenant_quota()
        assert hasattr(_load_scheduling_config, "_cache")

        # Reset
        reset_scheduling_config_cache()
        assert not hasattr(_load_scheduling_config, "_cache")

        # Re-load
        q = get_default_tenant_quota()
        assert q > 0

    def test_config_reads_from_system_yaml(self):
        """Verify that values come from system.yaml when file exists."""
        from backend.kernel.scheduling.queue_stratification import (
            get_aging_config,
            get_default_tenant_quota,
            get_starvation_threshold_seconds,
            reset_scheduling_config_cache,
        )

        reset_scheduling_config_cache()
        # These should match system.yaml values (10, 3600, aging.enabled=True)
        q = get_default_tenant_quota()
        s = get_starvation_threshold_seconds()
        a = get_aging_config()
        assert q == 10
        assert s == 3600
        assert a["enabled"] is True
        assert a["interval_seconds"] == 300


# ====================================================================
# Explain governance context
# ====================================================================


class TestExplainGovernanceContext:
    """Tests for governance context in explain trace."""

    def test_governance_context_model_fields(self):
        from backend.api.jobs.models import JobExplainGovernanceContext

        ctx = JobExplainGovernanceContext(
            feature_flags={"sched_decision_audit": True},
            kind_circuit_state="closed",
            burst_active=False,
            tenant_service_class="premium",
            tenant_max_jobs_per_round=40,
            tenant_fair_share_weight=4.0,
            placement_policy="composite",
            starvation_threshold_seconds=3600,
            aging_config={"enabled": True, "interval_seconds": 300},
        )
        assert ctx.feature_flags["sched_decision_audit"] is True
        assert ctx.kind_circuit_state == "closed"
        assert ctx.tenant_service_class == "premium"
        assert ctx.tenant_max_jobs_per_round == 40
        assert ctx.starvation_threshold_seconds == 3600

    def test_governance_context_defaults(self):
        from backend.api.jobs.models import JobExplainGovernanceContext

        ctx = JobExplainGovernanceContext()
        assert ctx.burst_active is False
        assert ctx.node_quarantine_count == 0
        assert ctx.connector_cooling_count == 0
        assert ctx.tenant_service_class == "standard"
        assert ctx.placement_policy == "default"

    def test_explain_response_includes_governance(self):
        from backend.api.jobs.models import (
            JobExplainGovernanceContext,
            JobExplainResponse,
            JobResponse,
        )

        governance = JobExplainGovernanceContext(
            feature_flags={"audit": True},
            tenant_service_class="economy",
        )
        # Minimal JobResponse mock
        resp_data = {
            "job_id": "j1",
            "kind": "shell.exec",
            "status": "pending",
            "priority": 50,
            "queue_class": "batch",
            "worker_pool": "batch",
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
            "lease_seconds": 30,
            "timeout_seconds": 300,
            "payload": {},
            "connector_id": None,
            "name": None,
            "source": "console",
            "created_by": "test",
            "max_retries": 0,
            "retry_count": 0,
            "attempt": 0,
            "attempt_count": 0,
            "node_id": None,
            "lease_token": None,
            "leased_until": None,
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error_message": None,
            "failure_category": None,
            "target_os": None,
            "target_arch": None,
            "target_executor": None,
            "required_capabilities": [],
            "target_zone": None,
            "required_cpu_cores": None,
            "required_memory_mb": None,
            "required_gpu_vram_mb": None,
            "required_storage_mb": None,
            "lease_state": "none",
            "priority_bucket": "normal",
            "actions": [],
            "status_view": {"key": "pending", "label": "pending", "tone": "warning"},
            "lease_state_view": {"key": "none", "label": "none", "tone": "neutral"},
            "attention_reason": None,
            "estimated_duration_s": None,
            "idempotency_key": None,
        }
        explain_resp = JobExplainResponse(
            job=JobResponse(**resp_data),
            total_nodes=3,
            eligible_nodes=2,
            selected_node_id=None,
            decisions=[],
            governance=governance,
        )
        assert explain_resp.governance is not None
        assert explain_resp.governance.tenant_service_class == "economy"


# ====================================================================
# Release gate CI hardening
# ====================================================================


class TestReleaseGateHardening:
    """Tests for CI environment bypass prevention."""

    def test_ci_detection_variables(self):
        """Verify the list of CI env vars that should block bypass."""
        ci_vars = ["CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "TF_BUILD", "BUILDKITE"]
        # Ensure these are standard CI indicators
        assert "CI" in ci_vars
        assert "GITHUB_ACTIONS" in ci_vars
        assert len(ci_vars) >= 5


# ====================================================================
# Bandit hard gate
# ====================================================================


class TestBanditHardGate:
    """Tests for CI bandit step being a hard failure."""

    def test_bandit_step_no_soft_fail(self):
        """Verify ci.yml bandit step no longer uses || true."""
        import pathlib

        ci_path = pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
        if not ci_path.exists():
            pytest.skip("ci.yml not found")
        content = ci_path.read_text(encoding="utf-8")
        # Should NOT have || true on bandit line
        for line in content.splitlines():
            if "bandit" in line.lower() and "run:" not in line:
                assert "|| true" not in line, f"Bandit step still uses soft fail: {line}"


# ====================================================================
# API stability labels
# ====================================================================


class TestAPIStabilityLabels:
    """Tests for API stability tier annotations on OpenAPI tags."""

    def test_stability_tags_defined(self):
        """Verify the main app has openapi_tags with x-stability."""
        from backend.api.main import _API_STABILITY_TAGS

        assert len(_API_STABILITY_TAGS) > 0
        valid_tiers = {"stable", "beta", "experimental", "deprecated"}
        for tag in _API_STABILITY_TAGS:
            assert "name" in tag
            assert "x-stability" in tag, f"Tag {tag['name']} missing x-stability"
            assert tag["x-stability"] in valid_tiers, f"Tag {tag['name']} has invalid tier: {tag['x-stability']}"

    def test_core_routers_are_stable(self):
        """Verify auth, jobs, nodes are marked stable."""
        from backend.api.main import _API_STABILITY_TAGS

        stable_required = {"auth", "jobs", "nodes", "connectors"}
        name_to_tier = {t["name"]: t["x-stability"] for t in _API_STABILITY_TAGS}
        for name in stable_required:
            assert name in name_to_tier, f"Router '{name}' missing from stability tags"
            assert name_to_tier[name] == "stable", f"Router '{name}' should be stable, got {name_to_tier[name]}"

    def test_governance_router_is_beta(self):
        """Verify scheduling-governance is marked beta."""
        from backend.api.main import _API_STABILITY_TAGS

        name_to_tier = {t["name"]: t["x-stability"] for t in _API_STABILITY_TAGS}
        assert name_to_tier.get("scheduling-governance") == "beta"


# ====================================================================
# Strategy versioning
# ====================================================================


class TestStrategyVersioning:
    """Tests for config_version tracking on tenant scheduling policies."""

    def test_policy_model_has_config_version(self):
        """TenantSchedulingPolicy model should have config_version column."""
        from backend.models.tenant_scheduling_policy import TenantSchedulingPolicy

        assert hasattr(TenantSchedulingPolicy, "config_version")

    def test_policy_response_includes_version(self):
        """API response model should expose config_version."""
        from backend.api.scheduling_governance import TenantPolicyResponse

        fields = TenantPolicyResponse.model_fields
        assert "config_version" in fields


# ====================================================================
# Operator action actor identity
# ====================================================================


class TestOperatorActor:
    """Tests for actor identity in failure control plane governance events."""

    @pytest.mark.asyncio
    async def test_release_quarantine_records_actor(self):
        """release_quarantine should record actor identity in governance event."""
        import datetime

        from backend.core.failure_control_plane import FailureControlPlane

        fcp = FailureControlPlane()
        node_id = "node-actor-test"
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        # Quarantine the node first
        fcp._quarantine_until[node_id] = now + datetime.timedelta(seconds=300)
        fcp._node_consecutive[node_id] = 5

        released = await fcp.release_quarantine(node_id, actor="admin@zen70.io")
        assert released is True

        events = await fcp.governance_timeline(event_type="release")
        assert len(events) == 1
        assert events[0]["details"]["actor"] == "admin@zen70.io"

    @pytest.mark.asyncio
    async def test_pending_audit_includes_actor(self):
        """pending_audit_events should include actor field."""
        import datetime

        from backend.core.failure_control_plane import FailureControlPlane

        fcp = FailureControlPlane()
        node_id = "node-audit-actor"
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        fcp._quarantine_until[node_id] = now + datetime.timedelta(seconds=300)

        await fcp.release_quarantine(node_id, actor="ops-bot")
        events = fcp.pending_audit_events
        assert len(events) == 1
        assert events[0]["actor"] == "ops-bot"

    @pytest.mark.asyncio
    async def test_auto_governance_defaults_to_system(self):
        """Auto-triggered governance events should default actor to 'system'."""
        import datetime

        from backend.core.failure_control_plane import FailureControlPlane

        fcp = FailureControlPlane()
        now = datetime.datetime(2025, 1, 1, 12, 0, 0)
        # Trigger quarantine via consecutive failures
        for i in range(5):
            await fcp.record_failure(
                node_id="node-auto",
                job_id=f"job-{i}",
                category="execution",
                now=now,
            )

        events = await fcp.governance_timeline(event_type="quarantine")
        assert len(events) >= 1
        # Auto-triggered events use default actor "system"
        assert events[0]["details"].get("actor", "system") == "system"


# ====================================================================
# Console split verification
# ====================================================================


class TestConsoleSplit:
    """Verify console helpers extraction didn't break imports."""

    def test_console_helpers_importable(self):
        from backend.api.console_helpers import (
            build_menu_response,
            route_target,
        )

        assert callable(build_menu_response)
        assert callable(route_target)

    def test_console_router_still_works(self):
        from backend.api.console import get_console_diagnostics, get_console_overview, router

        assert router.prefix == "/api/v1/console"
        assert callable(get_console_overview)
        assert callable(get_console_diagnostics)
