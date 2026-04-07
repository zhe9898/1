from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.aggregate_owner_registry import export_aggregate_owner_registry, unique_owner_service_map
from backend.core.architecture_governance import export_architecture_governance_rules, export_architecture_governance_snapshot
from backend.core.compatibility_adapter import export_status_compatibility_rules
from backend.core.control_plane import export_surface_registry
from backend.core.execution_fault_isolation import export_fault_isolation_contract
from backend.core.extension_guard import (
    assert_budgeted_payload,
    export_extension_budget_contract,
    validate_extension_manifest_contract,
    validate_scheduling_profile_budget,
)
from backend.core.kernel_capabilities import capability_keys
from backend.core.lease_service import export_lease_service_contract
from backend.core.runtime_policy_resolver import export_runtime_policy_contract
from backend.core.scheduling_framework import SchedulingProfile

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
RUNNER_ROOT = ROOT / "runner-agent"

_OWNER_MODULES_BY_FIELD: dict[tuple[str, str], set[str]] = {
    ("job", "status"): {
        "backend/core/job_lifecycle_service.py",
        "backend/core/lease_service.py",
    },
    ("job", "attempt"): {"backend/core/lease_service.py"},
    ("job", "lease_token"): {"backend/core/lease_service.py"},
    ("job", "leased_until"): {"backend/core/lease_service.py"},
    ("attempt", "status"): {"backend/core/lease_service.py"},
    ("attempt", "lease_token"): {"backend/core/lease_service.py"},
    ("attempt", "scheduling_decision_id"): {"backend/core/lease_service.py"},
    ("node", "enrollment_status"): {"backend/core/node_enrollment_service.py"},
    ("node", "drain_status"): {"backend/core/node_enrollment_service.py"},
    ("node", "drain_until"): {"backend/core/node_enrollment_service.py"},
    ("connector", "status"): {"backend/core/connector_service.py"},
    ("connector", "config"): {"backend/core/connector_service.py"},
    ("trigger", "status"): {"backend/core/trigger_command_service.py"},
    ("delivery", "status"): {"backend/core/trigger_command_service.py"},
    ("workflow", "status"): {"backend/core/workflow_command_service.py"},
    ("policy", "config_version"): {"backend/core/scheduling_policy_service.py"},
    ("flag", "enabled"): {"backend/core/feature_flag_service.py"},
    ("flag", "updated_by"): {"backend/core/feature_flag_service.py"},
}

_LEASE_ONLY_FIELDS: set[tuple[str, str]] = {
    ("job", "attempt"),
    ("job", "lease_token"),
    ("job", "leased_until"),
    ("attempt", "status"),
    ("attempt", "lease_token"),
    ("attempt", "scheduling_decision_id"),
}


def _python_sources(*folders: str) -> list[Path]:
    paths: list[Path] = []
    for folder in folders:
        paths.extend(sorted((BACKEND_ROOT / folder).rglob("*.py")))
    return paths


def _runner_text(*parts: str) -> str:
    return (RUNNER_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _flatten_targets(target: ast.expr) -> list[ast.expr]:
    if isinstance(target, (ast.Tuple, ast.List)):
        values: list[ast.expr] = []
        for item in target.elts:
            values.extend(_flatten_targets(item))
        return values
    return [target]


def _assignment_pairs(path: Path) -> list[tuple[int, tuple[str, str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    pairs: list[tuple[int, tuple[str, str]]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                targets.extend(_flatten_targets(target))
        elif isinstance(node, ast.AnnAssign):
            targets.extend(_flatten_targets(node.target))
        elif isinstance(node, ast.AugAssign):
            targets.extend(_flatten_targets(node.target))
        else:
            continue

        for target in targets:
            if not isinstance(target, ast.Attribute):
                continue
            if not isinstance(target.value, ast.Name):
                continue
            pair = (target.value.id, target.attr)
            if pair in _OWNER_MODULES_BY_FIELD:
                pairs.append((getattr(target, "lineno", 0), pair))
    return pairs


def test_surface_registry_exports_capability_scope_pack_and_policy_trace() -> None:
    registry = export_surface_registry()
    known_capabilities = set(capability_keys())

    assert registry
    for surface_key, record in registry.items():
        assert surface_key
        assert record["capability_key"] in known_capabilities
        assert record["capability_keys"] == [record["capability_key"]]
        assert record["required_scope"] == record["required_scopes"]
        assert isinstance(record["pack_id"], str) and record["pack_id"]
        assert record["policy_gate"] == record["policy_gates"]
        assert record["route_name"]
        assert record["route_path"]
        assert record["endpoint"].startswith("/v1/")


def test_aggregate_owner_registry_is_unique_and_complete() -> None:
    registry = export_aggregate_owner_registry()
    owner_map = unique_owner_service_map()

    assert registry
    assert owner_map["JobAggregate"] == "JobLifecycleService"
    assert owner_map["LeaseAggregate"] == "LeaseService"
    assert owner_map["NodeAggregate"] == "NodeEnrollmentService"
    assert owner_map["ConnectorAggregate"] == "ConnectorService"
    assert owner_map["TriggerAggregate"] == "TriggerCommandService"
    assert owner_map["WorkflowAggregate"] == "WorkflowCommandService"
    assert owner_map["SchedulingPolicyAggregate"] == "SchedulingPolicyService"
    assert owner_map["FeatureFlagAggregate"] == "FeatureFlagService"
    assert "jobs.leased_until" in registry["LeaseAggregate"]["owned_fields"]
    assert "job_attempts.status" in registry["LeaseAggregate"]["owned_fields"]
    assert len(owner_map) == len(set(owner_map.values()))


def test_status_compatibility_rules_export_release_window_metadata() -> None:
    rules = export_status_compatibility_rules()

    assert rules["nodes.enrollment_status"]["compatibility_window_releases"] == 0
    assert rules["triggers.status"]["aliases"] == {}
    assert rules["trigger_deliveries.status"]["aliases"] == {}
    assert rules["workflows.status"]["aliases"] == {}
    assert rules["jobs.status"]["aliases"] == {}
    assert rules["job_attempts.status"]["aliases"] == {}
    assert rules["workflow_steps.status"]["aliases"] == {}


def test_runtime_policy_gate_blocks_runtime_system_yaml_reads_outside_allowlist() -> None:
    allowlist = {
        "backend/core/scheduling_policy_store.py",
        "backend/sentinel/routing_operator.py",
    }
    violations: list[str] = []
    for path in _python_sources("api", "core", "workers", "sentinel"):
        rel = _rel(path)
        if rel in allowlist:
            continue
        source = path.read_text(encoding="utf-8")
        if "system.yaml" in source and ("read_text(" in source or "open(" in source):
            violations.append(rel)
    assert violations == []


def test_state_path_gate_only_allows_owner_services_for_core_field_writes() -> None:
    violations: list[str] = []
    for path in _python_sources("api", "core", "workers", "sentinel"):
        rel = _rel(path)
        for lineno, pair in _assignment_pairs(path):
            allowed = _OWNER_MODULES_BY_FIELD[pair]
            if rel not in allowed:
                violations.append(f"{rel}:{lineno}:{pair[0]}.{pair[1]}")
    assert violations == []


def test_lease_gate_only_allows_lease_service_writes() -> None:
    violations: list[str] = []
    for path in _python_sources("api", "core", "workers", "sentinel"):
        rel = _rel(path)
        for lineno, pair in _assignment_pairs(path):
            if pair not in _LEASE_ONLY_FIELDS:
                continue
            if rel != "backend/core/lease_service.py":
                violations.append(f"{rel}:{lineno}:{pair[0]}.{pair[1]}")
    assert violations == []


def test_runtime_policy_contract_exports_policy_store_entrypoint() -> None:
    contract = export_runtime_policy_contract()

    assert contract["entrypoint"] == "backend.core.runtime_policy_resolver.RuntimePolicyResolver"
    assert contract["policy_store_entrypoint"] == "backend.core.scheduling_policy_store.get_policy_store"
    assert contract["router_gate_method"] == "router_enabled"
    assert contract["snapshot_method"] == "snapshot"


def test_lease_service_contract_exports_owned_fields_and_rotation_semantics() -> None:
    contract = export_lease_service_contract()

    assert contract["entrypoint"] == "backend.core.lease_service.LeaseService"
    assert contract["grant_method"] == "grant_lease"
    assert contract["renew_method"] == "renew_lease"
    assert contract["rotates_lease_token_on_renew"] is True
    assert "jobs.leased_until" in contract["owned_fields"]
    assert "job_attempts.status" in contract["owned_fields"]


def test_extension_budget_contract_matches_guard_limits() -> None:
    contract = export_extension_budget_contract()

    assert contract["sync_execution_budget_ms"] == 100
    assert contract["async_execution_budget_ms"] == 500
    assert contract["payload_limit_bytes"] == 64 * 1024
    assert contract["audit_details_limit_bytes"] == 16 * 1024
    assert contract["max_plugins_per_phase"] == 4
    assert contract["max_plugins_total"] == 16
    assert contract["phase_defaults"]["post_bind"]["external_call_limit"] == 2
    assert contract["phase_defaults"]["filter"]["external_call_limit"] == 0


def test_architecture_governance_registry_is_code_backed_and_exportable() -> None:
    rules = export_architecture_governance_rules()
    snapshot = export_architecture_governance_snapshot()

    assert tuple(rules.keys()) == tuple(f"A{i}" for i in range(1, 12))
    assert rules["A1"]["maturity"] == "enforced"
    assert rules["A6"]["maturity"] == "enforced"
    assert "surface_registry" in snapshot["entrypoints"]
    assert snapshot["registries"]["surface_registry"] == export_surface_registry()
    assert snapshot["registries"]["fault_isolation_contract"] == export_fault_isolation_contract()
    assert snapshot["registries"]["aggregate_owner_registry"] == export_aggregate_owner_registry()
    assert snapshot["registries"]["status_compatibility_rules"] == export_status_compatibility_rules()


def test_fault_isolation_contract_matches_runner_and_api_sources() -> None:
    contract = export_fault_isolation_contract()
    poller_source = _runner_text("internal", "jobs", "poller.go")
    executor_source = _runner_text("internal", "exec", "executor.go")
    service_source = _runner_text("internal", "service", "service.go")
    api_client_source = _runner_text("internal", "api", "client.go")
    lifecycle_source = (BACKEND_ROOT / "api" / "jobs" / "lifecycle.py").read_text(encoding="utf-8")
    worker_source = (BACKEND_ROOT / "workers" / "control_plane_worker.py").read_text(encoding="utf-8")

    assert contract["runner_api_client_timeout_seconds"] == 30
    assert "DefaultAPIClientTimeout = 30 * time.Second" in api_client_source

    lease_renewal = contract["lease_renewal"]
    assert lease_renewal["min_interval_seconds"] == 5
    assert lease_renewal["failure_abandon_after"] == 3
    assert "renewEvery := time.Duration(max(5, job.LeaseSeconds/2)) * time.Second" in poller_source
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

    assert '_assert_valid_lease_owner(job, payload, "renew")' in lifecycle_source
    assert '_assert_valid_lease_owner(job, payload, "result")' in lifecycle_source
    assert '_assert_valid_lease_owner(job, payload, "fail")' in lifecycle_source
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
