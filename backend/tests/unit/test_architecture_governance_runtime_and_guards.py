from __future__ import annotations

from .architecture_governance_test_support import (
    BACKEND_ROOT,
    ROOT,
    SchedulingProfile,
    SimpleNamespace,
    _runner_text,
    assert_budgeted_payload,
    auth_boundary_violations,
    auth_tenant_boundary_violations,
    backend_domain_import_fence_violations,
    cookie_boundary_violations,
    development_cleanroom_violations,
    export_development_cleanroom_contract,
    export_fault_isolation_contract,
    pytest,
    tenant_claim_violations,
    validate_extension_manifest_contract,
    validate_scheduling_profile_budget,
)


def test_fault_isolation_contract_matches_runner_and_api_sources() -> None:
    contract = export_fault_isolation_contract()
    poller_source = _runner_text("internal", "jobs", "poller.go")
    executor_source = _runner_text("internal", "exec", "executor.go")
    service_source = _runner_text("internal", "service", "service.go")
    api_client_source = _runner_text("internal", "api", "client.go")
    lifecycle_route_source = (BACKEND_ROOT / "control_plane" / "adapters" / "jobs" / "lifecycle.py").read_text(encoding="utf-8")
    lifecycle_service_source = (BACKEND_ROOT / "control_plane" / "adapters" / "jobs" / "lifecycle_service.py").read_text(encoding="utf-8")
    worker_source = (BACKEND_ROOT / "workers" / "control_plane_worker.py").read_text(encoding="utf-8")

    assert contract["runner_api_client_timeout_seconds"] == 30
    assert "DefaultAPIClientTimeout = 30 * time.Second" in api_client_source

    lease_renewal = contract["lease_renewal"]
    assert lease_renewal["min_interval_seconds"] == 5
    assert lease_renewal["failure_abandon_after"] == 3
    assert "renewEvery := leaseRenewalInterval(jobSnapshot.LeaseSeconds)" in poller_source
    assert "job.applyRenewedLease(renewedJob)" in poller_source
    assert "const maxConsecutiveFailures = 3" in poller_source
    assert 'log.Printf("lease renewal failed %d times, abandoning job %s"' in poller_source
    assert "return context.WithTimeout(context.WithoutCancel(parent), reportingTimeout)" in poller_source

    reporting = contract["reporting"]
    assert reporting["timeout_seconds"] == 15
    assert "reportingTimeout          = 15 * time.Second" in poller_source

    graceful_shutdown = contract["graceful_shutdown"]
    assert graceful_shutdown["drain_timeout_seconds"] == 30
    assert "const drainCallTimeout = 30 * time.Second" in service_source
    assert "context.WithTimeout(context.WithoutCancel(ctx), drainCallTimeout)" in service_source

    execution_timeout = contract["execution_timeout"]
    assert execution_timeout["headroom_seconds"] == 5
    assert execution_timeout["default_timeout_seconds"] == 300
    assert "DefaultJobTimeoutSeconds = 300" in executor_source
    assert "if leaseSeconds > 10 {" in executor_source
    assert "return time.Duration(leaseSeconds-5) * time.Second" in executor_source

    assert "build_default_job_lifecycle_dependencies()" in lifecycle_route_source
    assert "deps.assert_valid_lease_owner(job, payload, action)" in lifecycle_service_source
    assert 'action="renew"' in lifecycle_service_source
    assert 'action="result"' in lifecycle_service_source
    assert 'action="fail"' in lifecycle_service_source
    assert 'asyncio.create_task(factory(redis_client), name=f"control-worker:{name}")' in worker_source


def test_extension_manifest_guard_requires_traceable_manifest_path() -> None:
    with pytest.raises(ValueError):
        validate_extension_manifest_contract(SimpleNamespace(extension_id="external.demo", source_manifest_path=None))


def test_extension_budget_guard_rejects_sync_plugin_over_budget() -> None:
    class SlowFilter:
        name = "slow-filter"
        execution_budget_ms = 101

    profile = SchedulingProfile(name="default", filters=[SlowFilter()])
    with pytest.raises(ValueError):
        validate_scheduling_profile_budget(profile)


def test_extension_budget_guard_rejects_post_bind_external_call_over_budget() -> None:
    class ChattyPostBind:
        name = "chatty-post-bind"
        external_call_limit = 3

    profile = SchedulingProfile(name="default", post_binders=[ChattyPostBind()])
    with pytest.raises(ValueError):
        validate_scheduling_profile_budget(profile)


def test_extension_payload_budget_guard_enforces_64kib_limit() -> None:
    oversized = {"blob": "x" * (70 * 1024)}
    with pytest.raises(ValueError):
        assert_budgeted_payload(oversized)


def test_domain_dependency_gate_blocks_new_reverse_imports() -> None:
    assert backend_domain_import_fence_violations(repo_root=ROOT) == []


def test_auth_boundary_gate_blocks_direct_role_claim_reads() -> None:
    assert auth_boundary_violations(repo_root=ROOT) == []


def test_auth_boundary_gate_blocks_direct_audit_helper_imports() -> None:
    assert auth_boundary_violations(repo_root=ROOT) == []


def test_tenant_claim_gate_blocks_direct_tenant_claim_reads() -> None:
    assert tenant_claim_violations(repo_root=ROOT) == []


def test_cookie_boundary_gate_blocks_raw_cookie_access() -> None:
    assert cookie_boundary_violations(repo_root=ROOT) == []


def test_auth_tenant_boundary_gate_blocks_default_tenant_fallbacks() -> None:
    assert auth_tenant_boundary_violations() == []


def test_development_cleanroom_contract_exports_forbidden_transition_markers() -> None:
    contract = export_development_cleanroom_contract()

    assert contract["development_phase"] is True
    assert contract["policy"] == "clean-room"
    assert "backend/runtime" in contract["governed_roots"]
    assert "runner-agent" in contract["governed_roots"]
    markers = contract["forbidden_transitional_markers"]
    assert markers["sanitized_legacy_docstring"] == ["Sanitized legacy docstring"]
    assert markers["compat_helper_prefix"] == ["compat_get_"]
    assert "drop-in async replacement" in markers["drop_in_replacement_phrase"]


def test_development_cleanroom_gate_has_no_transitional_markers() -> None:
    assert development_cleanroom_violations(repo_root=ROOT) == []
