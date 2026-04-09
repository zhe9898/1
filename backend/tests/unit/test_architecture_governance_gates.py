from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.kernel.capabilities.registry import capability_keys
from backend.kernel.contracts.status import export_status_compatibility_rules
from backend.kernel.execution.fault_isolation import export_fault_isolation_contract
from backend.kernel.execution.lease_service import export_lease_service_contract
from backend.kernel.extensions.extension_guard import (
    assert_budgeted_payload,
    export_extension_budget_contract,
    validate_extension_manifest_contract,
    validate_scheduling_profile_budget,
)
from backend.kernel.governance.aggregate_owner_registry import export_aggregate_owner_registry, unique_owner_service_map
from backend.kernel.governance.architecture_rules import (
    export_architecture_governance_rules,
    export_architecture_governance_snapshot,
)
from backend.kernel.policy.runtime_policy_resolver import export_runtime_policy_contract
from backend.kernel.scheduling.scheduling_framework import SchedulingProfile
from backend.kernel.surfaces.registry import export_surface_registry

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
RUNNER_ROOT = ROOT / "runner-agent"

_OWNER_MODULES_BY_FIELD: dict[tuple[str, str], set[str]] = {
    ("job", "status"): {
        "backend/kernel/execution/job_lifecycle_service.py",
        "backend/kernel/execution/lease_service.py",
    },
    ("job", "attempt"): {"backend/kernel/execution/lease_service.py"},
    ("job", "lease_token"): {"backend/kernel/execution/lease_service.py"},
    ("job", "leased_until"): {"backend/kernel/execution/lease_service.py"},
    ("attempt", "status"): {"backend/kernel/execution/lease_service.py"},
    ("attempt", "lease_token"): {"backend/kernel/execution/lease_service.py"},
    ("attempt", "scheduling_decision_id"): {"backend/kernel/execution/lease_service.py"},
    ("node", "enrollment_status"): {"backend/kernel/topology/node_enrollment_service.py"},
    ("node", "drain_status"): {"backend/kernel/topology/node_enrollment_service.py"},
    ("node", "drain_until"): {"backend/kernel/topology/node_enrollment_service.py"},
    ("connector", "status"): {"backend/kernel/extensions/connector_service.py"},
    ("connector", "config"): {"backend/kernel/extensions/connector_service.py"},
    ("trigger", "status"): {"backend/kernel/extensions/trigger_command_service.py"},
    ("delivery", "status"): {"backend/kernel/extensions/trigger_command_service.py"},
    ("workflow", "status"): {"backend/kernel/extensions/workflow_command_service.py"},
    ("policy", "config_version"): {"backend/kernel/scheduling/scheduling_policy_service.py"},
    ("flag", "enabled"): {"backend/kernel/policy/feature_flag_service.py"},
    ("flag", "updated_by"): {"backend/kernel/policy/feature_flag_service.py"},
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
        "backend/kernel/policy/policy_store.py",
        "backend/sentinel/routing_operator.py",
    }
    violations: list[str] = []
    for path in _python_sources("api", "control_plane", "core", "kernel", "runtime", "workers", "sentinel"):
        rel = _rel(path)
        if rel in allowlist:
            continue
        source = path.read_text(encoding="utf-8")
        if "system.yaml" in source and ("read_text(" in source or "open(" in source):
            violations.append(rel)
    assert violations == []


def test_platform_infra_gate_blocks_legacy_core_imports() -> None:
    blocked = (
        "backend.core.runtime_support",
        "backend.core.telemetry",
        "backend.core.metrics",
        "backend.core.db_locks",
        "backend.core.alembic_runtime",
        "backend.core.secret_envelope",
        "backend.core.security_redaction",
        "backend.core.scheduling_policy_types",
        "backend.core.scheduling_policy_validation",
        "backend.core.governance_facade",
        "backend.core.failure_control_plane",
        "backend.core.scheduling_governance",
        "backend.core.scheduling_policy_service",
        "backend.core.scheduler_auto_tune",
        "backend.core.scheduler_auto_tune_audit",
        "backend.core.scheduler_auto_tune_state",
        "backend.core.scheduling_framework",
        "backend.core.worker_pool",
        "backend.core.version",
        "backend.core.connector_secret_service",
        "backend.core.security_policy",
        "backend.core.errors",
        "backend.core.safe_error_projection",
        "backend.core.protocol_version",
        "backend.core.workload_semantics",
        "backend.core.alert_actions",
        "backend.core.auth_helpers",
        "backend.core.jwt",
        "backend.core.permissions",
        "backend.core.sessions",
        "backend.core.webauthn",
        "backend.core.webauthn_challenge_store",
        "backend.core.webauthn_flow_session",
        "backend.core.rls",
        "backend.core.job_concurrency_service",
        "backend.core.job_type_separation",
        "backend.core.quota",
        "backend.core.feature_flag_service",
        "backend.core.control_plane_state",
        "backend.core.device_profiles",
        "backend.core.user_lifecycle",
        "backend.core.webhooks",
        "backend.core.alerting",
        "backend.core.events_schema",
        "backend.core.gen_grpc",
        "backend.core.config",
        "backend.core.data_retention",
        "backend.core.migration_schema_guard",
        "backend.core.migration_governance",
        "backend.core.migration_runner",
        "backend.core.status_contracts",
        "backend.core.audit_logging",
        "backend.core.ai_providers",
    )
    violations: list[str] = []
    for path in _python_sources("api", "control_plane", "core", "kernel", "runtime", "workers", "sentinel"):
        rel = _rel(path)
        source = path.read_text(encoding="utf-8")
        for module in blocked:
            if module in source:
                violations.append(f"{rel}:{module}")
    assert violations == []


def test_platform_redis_gate_blocks_sdk_imports_outside_platform() -> None:
    violations: list[str] = []
    for path in _python_sources("api", "control_plane", "core", "kernel", "runtime", "workers", "sentinel"):
        rel = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "redis" or alias.name.startswith("redis.") for alias in node.names):
                    violations.append(f"{rel}:{getattr(node, 'lineno', 0)}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "redis" or (node.module or "").startswith("redis."):
                    violations.append(f"{rel}:{getattr(node, 'lineno', 0)}")
    assert violations == []


def test_platform_redis_gate_blocks_client_escape_hatch_usage() -> None:
    violations: list[str] = []
    for path in _python_sources("api", "control_plane", "core", "kernel", "runtime", "workers", "sentinel"):
        rel = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "redis":
                continue
            parent = parents.get(node)
            if not isinstance(parent, ast.Attribute):
                continue
            if isinstance(node.value, ast.Attribute) and node.value.attr == "state":
                continue
            violations.append(f"{rel}:{getattr(node, 'lineno', 0)}")
    assert violations == []


def test_platform_redis_gate_blocks_client_module_escape_imports() -> None:
    violations: list[str] = []
    for path in _python_sources("api", "control_plane", "core", "kernel", "runtime", "workers", "sentinel"):
        rel = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "backend.platform.redis.client":
                continue
            if any(alias.name == "redis" for alias in node.names):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}")
    assert violations == []


def test_ai_gateway_prompt_policy_stays_in_control_plane_auth_boundary() -> None:
    ai_router_path = BACKEND_ROOT / "ai_router.py"
    source = ai_router_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ai_router_path))

    ai_policy_import_seen = False
    forbidden_imports = {
        "backend.control_plane.auth.role_claims",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "backend.control_plane.auth.ai_policy":
                imported_names = {alias.name for alias in node.names}
                assert imported_names == {"apply_prompt_override", "resolve_ai_proxy_policy"}
                ai_policy_import_seen = True
            if node.module in forbidden_imports:
                message = "backend/ai_router.py must not bypass AI policy by importing role-claim helpers directly: " f"{node.module}"
                raise AssertionError(message)

    assert ai_policy_import_seen, "backend/ai_router.py must import AI prompt policy through control_plane.auth.ai_policy"

    forbidden_literals = (
        "family learning guide",
        "Never invent concrete device IDs",
        '"intent":"device_control"',
        "light_living_1",
    )
    for literal in forbidden_literals:
        assert literal not in source, (
            "backend/ai_router.py must not embed role-specific prompt text or device-control schema; "
            "keep those contracts in backend/control_plane/auth/ai_policy.py"
        )

    forbidden_role_access = (
        'current_user.get("role")',
        "current_user.get('role')",
        'current_user["role"]',
        "current_user['role']",
    )
    for pattern in forbidden_role_access:
        assert pattern not in source, "backend/ai_router.py must not branch on role claims directly; " "use resolve_ai_proxy_policy() instead"


def test_state_path_gate_only_allows_owner_services_for_core_field_writes() -> None:
    violations: list[str] = []
    for path in _python_sources("api", "control_plane", "core", "kernel", "workers"):
        rel = _rel(path)
        for lineno, pair in _assignment_pairs(path):
            allowed = _OWNER_MODULES_BY_FIELD[pair]
            if rel not in allowed:
                violations.append(f"{rel}:{lineno}:{pair[0]}.{pair[1]}")
    assert violations == []


def test_lease_gate_only_allows_lease_service_writes() -> None:
    violations: list[str] = []
    for path in _python_sources("api", "control_plane", "core", "kernel", "workers"):
        rel = _rel(path)
        for lineno, pair in _assignment_pairs(path):
            if pair not in _LEASE_ONLY_FIELDS:
                continue
            if rel != "backend/kernel/execution/lease_service.py":
                violations.append(f"{rel}:{lineno}:{pair[0]}.{pair[1]}")
    assert violations == []


def test_runtime_policy_contract_exports_policy_store_entrypoint() -> None:
    contract = export_runtime_policy_contract()

    assert contract["entrypoint"] == "backend.kernel.policy.runtime_policy_resolver.RuntimePolicyResolver"
    assert contract["policy_store_entrypoint"] == "backend.kernel.policy.policy_store.get_policy_store"
    assert contract["profile_normalizer"] == "backend.kernel.profiles.public_profile.normalize_gateway_profile"
    assert contract["runtime_pack_resolver"] == "backend.kernel.topology.profile_selection.resolve_runtime_pack_keys"
    assert contract["router_gate_method"] == "router_enabled"
    assert contract["snapshot_method"] == "snapshot"


def test_lease_service_contract_exports_owned_fields_and_rotation_semantics() -> None:
    contract = export_lease_service_contract()

    assert contract["entrypoint"] == "backend.kernel.execution.lease_service.LeaseService"
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
    assert snapshot["entrypoints"]["aggregate_owner_registry"] == "backend.kernel.governance.aggregate_owner_registry.export_aggregate_owner_registry"
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
    lifecycle_route_source = (BACKEND_ROOT / "api" / "jobs" / "lifecycle.py").read_text(encoding="utf-8")
    lifecycle_service_source = (BACKEND_ROOT / "api" / "jobs" / "lifecycle_service.py").read_text(encoding="utf-8")
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
