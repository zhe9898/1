from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.control_plane.auth.authority_boundary import export_auth_boundary_contract
from backend.extensions.extension_guard import (
    assert_budgeted_payload,
    export_extension_budget_contract,
    validate_extension_manifest_contract,
    validate_scheduling_profile_budget,
)
from backend.kernel.capabilities.registry import capability_keys
from backend.kernel.contracts.status import export_status_compatibility_rules
from backend.kernel.governance.aggregate_owner_registry import export_aggregate_owner_registry, unique_owner_service_map
from backend.kernel.governance.architecture_rules import (
    export_architecture_governance_rules,
    export_architecture_governance_snapshot,
)
from backend.kernel.governance.development_cleanroom import export_development_cleanroom_contract
from backend.kernel.governance.domain_import_fence import export_backend_domain_import_fence
from backend.kernel.policy.runtime_policy_resolver import export_runtime_policy_contract
from backend.kernel.surfaces.registry import export_surface_registry
from backend.platform.events.channels import export_event_channel_contract, tenant_realtime_subject, tenant_subject_token
from backend.platform.redis.runtime_state import export_runtime_state_contract
from backend.runtime.execution.fault_isolation import export_fault_isolation_contract
from backend.runtime.execution.lease_service import export_lease_service_contract
from backend.runtime.scheduling.scheduling_framework import SchedulingProfile
from backend.runtime.topology.runtime_contracts import control_plane_persona_keys, export_runtime_contract_taxonomy
from tools.auth_boundary_guard import auth_boundary_violations
from tools.auth_tenant_boundary_guard import auth_tenant_boundary_violations
from tools.backend_domain_fence import backend_domain_import_fence_violations
from tools.cookie_boundary_guard import cookie_boundary_violations
from tools.development_cleanroom_guard import development_cleanroom_violations
from tools.tenant_claim_guard import tenant_claim_violations

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
RUNNER_ROOT = ROOT / "runner-agent"
SCANNED_SOURCE_FOLDERS = ("control_plane", "core", "kernel", "runtime", "extensions", "workers", "sentinel")

_OWNER_MODULES_BY_FIELD: dict[tuple[str, str], set[str]] = {
    ("job", "status"): {
        "backend/runtime/execution/job_lifecycle_service.py",
        "backend/runtime/execution/lease_service.py",
    },
    ("job", "attempt"): {"backend/runtime/execution/lease_service.py"},
    ("job", "lease_token"): {"backend/runtime/execution/lease_service.py"},
    ("job", "leased_until"): {"backend/runtime/execution/lease_service.py"},
    ("attempt", "status"): {"backend/runtime/execution/lease_service.py"},
    ("attempt", "lease_token"): {"backend/runtime/execution/lease_service.py"},
    ("attempt", "scheduling_decision_id"): {"backend/runtime/execution/lease_service.py"},
    ("node", "enrollment_status"): {"backend/runtime/topology/node_enrollment_service.py"},
    ("node", "drain_status"): {"backend/runtime/topology/node_enrollment_service.py"},
    ("node", "drain_until"): {"backend/runtime/topology/node_enrollment_service.py"},
    ("connector", "status"): {"backend/extensions/connector_service.py"},
    ("connector", "config"): {"backend/extensions/connector_service.py"},
    ("trigger", "status"): {"backend/extensions/trigger_command_service.py"},
    ("delivery", "status"): {"backend/extensions/trigger_command_service.py"},
    ("workflow", "status"): {"backend/extensions/workflow_command_service.py"},
    ("policy", "config_version"): {"backend/runtime/scheduling/scheduling_policy_service.py"},
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


def _expr_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_expr_chain(node.value), node.attr)
    if isinstance(node, ast.Call):
        return _expr_chain(node.func)
    return ()


def _assignment_pairs(path: Path) -> list[tuple[int, tuple[str, str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    pairs: list[tuple[int, tuple[str, str]]] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                targets.extend(_flatten_targets(target))
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
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


def _dict_literal_string_keys(node: ast.AST | None) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _function_def(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


def _call_line(function_def: ast.AsyncFunctionDef | ast.FunctionDef, call_name: str) -> int | None:
    matching_lines = [
        getattr(child, "lineno", 0)
        for child in ast.walk(function_def)
        if isinstance(child, ast.Call) and _expr_chain(child.func)[-1:] == (call_name,)
    ]
    if not matching_lines:
        return None
    return min(matching_lines)


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
    for path in _python_sources(*SCANNED_SOURCE_FOLDERS):
        rel = _rel(path)
        if rel in allowlist:
            continue
        source = path.read_text(encoding="utf-8")
        if "system.yaml" in source and ("read_text(" in source or "open(" in source):
            violations.append(rel)
    assert violations == []


def test_backfill_reservation_boundary_keeps_policy_module_free_of_adapter_bootstrap() -> None:
    policy_path = BACKEND_ROOT / "runtime" / "scheduling" / "backfill_scheduling.py"
    factory_path = BACKEND_ROOT / "runtime" / "scheduling" / "reservation_store_factory.py"
    policy_source = policy_path.read_text(encoding="utf-8-sig")
    factory_source = factory_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(policy_source, filename=str(policy_path))

    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    class_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }

    assert "os" not in imports
    assert "urllib.parse" not in imports
    assert "backend.platform.redis" not in imports
    assert "ZEN70_RESERVATION_STORE" not in policy_source
    assert "build_reservation_store_from_env" in policy_source
    assert {"ResourceReservation", "ReservationStore", "InMemoryReservationStore", "RedisReservationStore"}.isdisjoint(class_names)
    assert "ZEN70_RESERVATION_STORE" in factory_source
    assert "SyncRedisClient" in factory_source


def test_pull_dispatch_boundary_keeps_candidate_window_and_contracts_out_of_pull_service() -> None:
    service_path = BACKEND_ROOT / "control_plane" / "adapters" / "jobs" / "pull_service.py"
    candidates_path = BACKEND_ROOT / "control_plane" / "adapters" / "jobs" / "pull_candidates.py"
    contracts_path = BACKEND_ROOT / "control_plane" / "adapters" / "jobs" / "pull_contracts.py"

    service_source = service_path.read_text(encoding="utf-8-sig")
    candidates_source = candidates_path.read_text(encoding="utf-8-sig")
    contracts_source = contracts_path.read_text(encoding="utf-8-sig")

    assert "from .pull_candidates import (" in service_source
    assert "from .pull_contracts import (" in service_source
    assert "def _build_candidate_context" not in service_source
    assert "def _query_dispatch_candidates" not in service_source
    assert "class PullJobsDependencies" not in service_source
    assert "class PullRuntimeContext" not in service_source

    assert "def _build_candidate_context" in candidates_source
    assert "def _query_dispatch_candidates" in candidates_source
    assert "class PullJobsDependencies" in contracts_source
    assert "class PullRuntimeContext" in contracts_source


def test_extension_sdk_boundary_keeps_contracts_loader_registry_and_bootstrap_out_of_facade() -> None:
    sdk_path = BACKEND_ROOT / "extensions" / "extension_sdk.py"
    contracts_path = BACKEND_ROOT / "extensions" / "extension_contracts.py"
    loader_path = BACKEND_ROOT / "extensions" / "extension_manifest_loader.py"
    loader_contracts_path = BACKEND_ROOT / "extensions" / "extension_manifest_file_contracts.py"
    loader_parser_path = BACKEND_ROOT / "extensions" / "extension_manifest_parser.py"
    loader_projection_path = BACKEND_ROOT / "extensions" / "extension_manifest_projection.py"
    loader_schema_refs_path = BACKEND_ROOT / "extensions" / "extension_manifest_schema_refs.py"
    loader_source_path = BACKEND_ROOT / "extensions" / "extension_manifest_source.py"
    registry_path = BACKEND_ROOT / "extensions" / "extension_registry.py"
    registry_projection_path = BACKEND_ROOT / "extensions" / "extension_registry_projection.py"
    registry_bootstrap_writer_path = BACKEND_ROOT / "extensions" / "extension_registry_bootstrap_writer.py"
    registry_kind_writer_path = BACKEND_ROOT / "extensions" / "extension_registry_kind_writer.py"
    registry_state_path = BACKEND_ROOT / "extensions" / "extension_registry_state.py"
    registry_template_writer_path = BACKEND_ROOT / "extensions" / "extension_registry_template_writer.py"
    registry_writer_path = BACKEND_ROOT / "extensions" / "extension_registry_writer.py"
    bootstrap_path = BACKEND_ROOT / "extensions" / "extension_bootstrap.py"
    bootstrap_builtins_path = BACKEND_ROOT / "extensions" / "extension_bootstrap_builtins.py"
    bootstrap_state_path = BACKEND_ROOT / "extensions" / "extension_bootstrap_state.py"
    bootstrap_validation_path = BACKEND_ROOT / "extensions" / "extension_bootstrap_validation.py"
    builtin_catalog_path = BACKEND_ROOT / "extensions" / "extension_builtin_catalog.py"
    builtin_kinds_path = BACKEND_ROOT / "extensions" / "extension_builtin_kinds.py"
    builtin_job_kinds_compute_path = BACKEND_ROOT / "extensions" / "extension_builtin_job_kinds_compute.py"
    builtin_job_kinds_control_path = BACKEND_ROOT / "extensions" / "extension_builtin_job_kinds_control.py"
    builtin_job_kinds_integration_path = BACKEND_ROOT / "extensions" / "extension_builtin_job_kinds_integration.py"
    builtin_job_kinds_path = BACKEND_ROOT / "extensions" / "extension_builtin_job_kinds.py"
    builtin_connector_kinds_path = BACKEND_ROOT / "extensions" / "extension_builtin_connector_kinds.py"
    builtin_templates_path = BACKEND_ROOT / "extensions" / "extension_builtin_templates.py"
    builtin_template_contracts_path = BACKEND_ROOT / "extensions" / "extension_builtin_template_contracts.py"

    sdk_source = sdk_path.read_text(encoding="utf-8-sig")
    contracts_source = contracts_path.read_text(encoding="utf-8-sig")
    loader_source = loader_path.read_text(encoding="utf-8-sig")
    loader_contracts_source = loader_contracts_path.read_text(encoding="utf-8-sig")
    loader_parser_source = loader_parser_path.read_text(encoding="utf-8-sig")
    loader_projection_source = loader_projection_path.read_text(encoding="utf-8-sig")
    loader_schema_refs_source = loader_schema_refs_path.read_text(encoding="utf-8-sig")
    loader_io_source = loader_source_path.read_text(encoding="utf-8-sig")
    registry_source = registry_path.read_text(encoding="utf-8-sig")
    registry_projection_source = registry_projection_path.read_text(encoding="utf-8-sig")
    registry_bootstrap_writer_source = registry_bootstrap_writer_path.read_text(encoding="utf-8-sig")
    registry_kind_writer_source = registry_kind_writer_path.read_text(encoding="utf-8-sig")
    registry_state_source = registry_state_path.read_text(encoding="utf-8-sig")
    registry_template_writer_source = registry_template_writer_path.read_text(encoding="utf-8-sig")
    registry_writer_source = registry_writer_path.read_text(encoding="utf-8-sig")
    bootstrap_source = bootstrap_path.read_text(encoding="utf-8-sig")
    bootstrap_builtins_source = bootstrap_builtins_path.read_text(encoding="utf-8-sig")
    bootstrap_state_source = bootstrap_state_path.read_text(encoding="utf-8-sig")
    bootstrap_validation_source = bootstrap_validation_path.read_text(encoding="utf-8-sig")
    builtin_catalog_source = builtin_catalog_path.read_text(encoding="utf-8-sig")
    builtin_kinds_source = builtin_kinds_path.read_text(encoding="utf-8-sig")
    builtin_job_kinds_compute_source = builtin_job_kinds_compute_path.read_text(encoding="utf-8-sig")
    builtin_job_kinds_control_source = builtin_job_kinds_control_path.read_text(encoding="utf-8-sig")
    builtin_job_kinds_integration_source = builtin_job_kinds_integration_path.read_text(encoding="utf-8-sig")
    builtin_job_kinds_source = builtin_job_kinds_path.read_text(encoding="utf-8-sig")
    builtin_connector_kinds_source = builtin_connector_kinds_path.read_text(encoding="utf-8-sig")
    builtin_templates_source = builtin_templates_path.read_text(encoding="utf-8-sig")
    builtin_template_contracts_source = builtin_template_contracts_path.read_text(encoding="utf-8-sig")

    assert "from .extension_contracts import (" in sdk_source
    assert "from .extension_manifest_loader import DEFAULT_EXTENSION_MANIFESTS_DIR, ExtensionManifestLoader" in sdk_source
    assert "from .extension_registry import ExtensionRegistry" in sdk_source
    assert "from .extension_bootstrap import ExtensionBootstrapper" in sdk_source
    assert "class _FileExtensionManifest" not in sdk_source
    assert "class ExtensionManifestLoader" not in sdk_source
    assert "class ExtensionRegistry" not in sdk_source
    assert "class ExtensionBootstrapper" not in sdk_source
    assert "def parse_file_manifest" not in sdk_source

    assert "class ExtensionManifest" in contracts_source
    assert "def validate_extension_manifest_versions" in contracts_source
    assert "from .extension_manifest_parser import parse_file_manifest" in loader_source
    assert "from .extension_manifest_source import DEFAULT_EXTENSION_MANIFESTS_DIR, ExtensionManifestSource" in loader_source
    assert "class ExtensionManifestLoader" in loader_source
    assert "class FileExtensionManifest" not in loader_source
    assert "def load_model_ref" not in loader_source
    assert "def read_manifest_file" not in loader_source
    assert "class FileExtensionManifest" in loader_contracts_source
    assert "from .extension_manifest_projection import project_file_manifest" in loader_parser_source
    assert "def load_model_ref" not in loader_parser_source
    assert "def project_file_manifest" in loader_projection_source
    assert "def load_model_ref" in loader_schema_refs_source
    assert "class ExtensionManifestSource" in loader_io_source
    assert "class ExtensionRegistry" in registry_source
    assert "from .extension_registry_state import ExtensionRegistryState" in registry_source
    assert "from .extension_registry_writer import ExtensionRegistryWriter" in registry_source
    assert "def clear_extensions_except" in registry_source
    assert "def register_external_manifests" in registry_source
    assert "register_job_kind(" not in registry_source
    assert "register_connector_kind(" not in registry_source
    assert "register_workflow_template(" not in registry_source
    assert "def build_extension_info" in registry_projection_source
    assert "def build_extension_kind_metadata" in registry_projection_source
    assert "class ExtensionRegistryBootstrapWriter" in registry_bootstrap_writer_source
    assert "class ExtensionKindRegistryWriter" in registry_kind_writer_source
    assert "class ExtensionRegistryState" in registry_state_source
    assert "class ExtensionTemplateRegistryWriter" in registry_template_writer_source
    assert "class ExtensionRegistryWriter" in registry_writer_source
    assert "class ExtensionBootstrapper" in bootstrap_source
    assert "from .extension_bootstrap_builtins import ExtensionBuiltinBootstrap" in bootstrap_source
    assert "from .extension_bootstrap_state import ExtensionBootstrapState" in bootstrap_source
    assert "from .extension_bootstrap_validation import ExtensionBootstrapValidator" in bootstrap_source
    assert "clear_extensions_except(" in bootstrap_source
    assert "register_external_manifests(" in bootstrap_source
    assert "register_manifest_kinds(" not in bootstrap_source
    assert "register_manifest_templates(" not in bootstrap_source
    assert ".manifests[" not in bootstrap_source
    assert "best_effort_semver" not in bootstrap_source
    assert "build_core_extension_manifest" not in bootstrap_source
    assert "get_runtime_version" not in bootstrap_source
    assert "class ExtensionBuiltinBootstrap" in bootstrap_builtins_source
    assert "class ExtensionBootstrapState" in bootstrap_state_source
    assert "class ExtensionBootstrapValidator" in bootstrap_validation_source
    assert "def build_core_extension_manifest" in builtin_catalog_source
    assert "from .extension_builtin_kinds import build_core_connector_kinds, build_core_job_kinds" in builtin_catalog_source
    assert "from .extension_builtin_templates import build_core_workflow_templates" in builtin_catalog_source
    assert "class HttpHealthcheckTemplateParams" not in builtin_catalog_source
    assert "def _core_job_kinds" not in builtin_catalog_source
    assert "def _core_workflow_templates" not in builtin_catalog_source
    assert "from .extension_builtin_job_kinds import build_core_job_kinds" in builtin_kinds_source
    assert "from .extension_builtin_connector_kinds import build_core_connector_kinds" in builtin_kinds_source
    assert "def build_core_job_kinds" not in builtin_kinds_source
    assert "def build_core_connector_kinds" not in builtin_kinds_source
    assert "from .extension_builtin_job_kinds_compute import build_core_compute_job_kinds" in builtin_job_kinds_source
    assert "from .extension_builtin_job_kinds_control import build_core_control_job_kinds" in builtin_job_kinds_source
    assert "from .extension_builtin_job_kinds_integration import build_core_integration_job_kinds" in builtin_job_kinds_source
    assert "def build_core_job_kinds" in builtin_job_kinds_source
    assert "def build_core_compute_job_kinds" in builtin_job_kinds_compute_source
    assert "def build_core_control_job_kinds" in builtin_job_kinds_control_source
    assert "def build_core_integration_job_kinds" in builtin_job_kinds_integration_source
    assert "def build_core_connector_kinds" in builtin_connector_kinds_source
    assert "def build_core_workflow_templates" in builtin_templates_source
    assert "class HttpHealthcheckTemplateParams" in builtin_template_contracts_source


def test_extension_manifest_loader_boundary_keeps_file_contracts_parser_and_source_out_of_loader() -> None:
    loader_path = BACKEND_ROOT / "extensions" / "extension_manifest_loader.py"
    source = loader_path.read_text(encoding="utf-8-sig")

    assert "from .extension_manifest_parser import parse_file_manifest" in source
    assert "from .extension_manifest_source import DEFAULT_EXTENSION_MANIFESTS_DIR, ExtensionManifestSource" in source
    assert "class FileExtensionManifest" not in source
    assert "def load_model_ref" not in source
    assert "json.loads(" not in source
    assert "yaml.safe_load(" not in source


def test_extension_manifest_parser_boundary_keeps_schema_resolution_and_projection_out_of_parser() -> None:
    parser_path = BACKEND_ROOT / "extensions" / "extension_manifest_parser.py"
    source = parser_path.read_text(encoding="utf-8-sig")

    assert "from .extension_manifest_projection import project_file_manifest" in source
    assert "FileExtensionManifest.model_validate(payload)" in source
    assert "def load_model_ref" not in source
    assert "import importlib" not in source
    assert "CompatibilityPolicy(" not in source
    assert "JobKindSpec(" not in source
    assert "ConnectorKindSpec(" not in source
    assert "WorkflowTemplateSpec(" not in source


def test_extension_registry_boundary_keeps_state_writer_projection_and_version_validation_out_of_registry() -> None:
    registry_path = BACKEND_ROOT / "extensions" / "extension_registry.py"
    source = registry_path.read_text(encoding="utf-8-sig")

    assert "from .extension_registry_projection import build_extension_info" in source
    assert "from .extension_registry_state import ExtensionRegistryState" in source
    assert "from .extension_registry_writer import ExtensionRegistryWriter" in source
    assert "def _kind_metadata" not in source
    assert '"extension_id": manifest.extension_id' not in source
    assert '"schema_version": schema_version' not in source
    assert "register_job_kind(" not in source
    assert "register_connector_kind(" not in source
    assert "register_workflow_template(" not in source


def test_extension_registry_writer_boundary_keeps_kind_template_and_bootstrap_writes_out_of_writer_orchestrator() -> None:
    writer_path = BACKEND_ROOT / "extensions" / "extension_registry_writer.py"
    source = writer_path.read_text(encoding="utf-8-sig")

    assert "from .extension_registry_bootstrap_writer import ExtensionRegistryBootstrapWriter" in source
    assert "from .extension_registry_kind_writer import ExtensionKindRegistryWriter" in source
    assert "from .extension_registry_template_writer import ExtensionTemplateRegistryWriter" in source
    assert "register_job_kind(" not in source
    assert "register_connector_kind(" not in source
    assert "register_workflow_template(" not in source
    assert "unregister_job_kind(" not in source
    assert "unregister_connector_kind(" not in source
    assert "unregister_workflow_template(" not in source


def test_extension_bootstrap_boundary_keeps_builtin_registration_validation_and_state_out_of_orchestrator() -> None:
    bootstrap_path = BACKEND_ROOT / "extensions" / "extension_bootstrap.py"
    source = bootstrap_path.read_text(encoding="utf-8-sig")

    assert "from .extension_bootstrap_builtins import ExtensionBuiltinBootstrap" in source
    assert "from .extension_bootstrap_state import ExtensionBootstrapState" in source
    assert "from .extension_bootstrap_validation import ExtensionBootstrapValidator" in source
    assert "best_effort_semver" not in source
    assert "build_core_extension_manifest" not in source
    assert "get_runtime_version" not in source
    assert "builtins_registered:" not in source
    assert "bootstrapped_manifest_dir:" not in source
    assert 'extension_id == "zen70.core"' not in source


def test_extension_builtin_catalog_boundary_keeps_builtin_contracts_and_catalog_entries_out_of_assembler() -> None:
    catalog_path = BACKEND_ROOT / "extensions" / "extension_builtin_catalog.py"
    source = catalog_path.read_text(encoding="utf-8-sig")

    assert "from .extension_builtin_kinds import build_core_connector_kinds, build_core_job_kinds" in source
    assert "from .extension_builtin_templates import build_core_workflow_templates" in source
    assert "class HttpHealthcheckTemplateParams" not in source
    assert "def _core_job_kinds" not in source
    assert "def _core_connector_kinds" not in source
    assert "def _core_workflow_templates" not in source


def test_extension_builtin_kinds_boundary_keeps_job_and_connector_catalogs_out_of_facade() -> None:
    builtin_kinds_path = BACKEND_ROOT / "extensions" / "extension_builtin_kinds.py"
    source = builtin_kinds_path.read_text(encoding="utf-8-sig")

    assert "from .extension_builtin_job_kinds import build_core_job_kinds" in source
    assert "from .extension_builtin_connector_kinds import build_core_connector_kinds" in source
    assert "def build_core_job_kinds" not in source
    assert "def build_core_connector_kinds" not in source


def test_extension_builtin_job_kinds_boundary_keeps_domain_catalog_entries_out_of_aggregator() -> None:
    job_kinds_path = BACKEND_ROOT / "extensions" / "extension_builtin_job_kinds.py"
    source = job_kinds_path.read_text(encoding="utf-8-sig")

    assert "from .extension_builtin_job_kinds_compute import build_core_compute_job_kinds" in source
    assert "from .extension_builtin_job_kinds_control import build_core_control_job_kinds" in source
    assert "from .extension_builtin_job_kinds_integration import build_core_integration_job_kinds" in source
    assert "ShellExecPayload" not in source
    assert "HttpRequestPayload" not in source
    assert "CronTickPayload" not in source
    assert "FileTransferPayload" not in source


def test_extension_sdk_internal_modules_do_not_leak_past_extension_boundary() -> None:
    internal_modules = {
        "backend.extensions.extension_contracts",
        "backend.extensions.extension_manifest_loader",
        "backend.extensions.extension_manifest_file_contracts",
        "backend.extensions.extension_manifest_parser",
        "backend.extensions.extension_manifest_projection",
        "backend.extensions.extension_manifest_schema_refs",
        "backend.extensions.extension_manifest_source",
        "backend.extensions.extension_registry",
        "backend.extensions.extension_registry_projection",
        "backend.extensions.extension_registry_bootstrap_writer",
        "backend.extensions.extension_registry_kind_writer",
        "backend.extensions.extension_registry_state",
        "backend.extensions.extension_registry_template_writer",
        "backend.extensions.extension_registry_writer",
        "backend.extensions.extension_bootstrap",
        "backend.extensions.extension_bootstrap_builtins",
        "backend.extensions.extension_bootstrap_state",
        "backend.extensions.extension_bootstrap_validation",
        "backend.extensions.extension_builtin_catalog",
        "backend.extensions.extension_builtin_kinds",
        "backend.extensions.extension_builtin_job_kinds_compute",
        "backend.extensions.extension_builtin_job_kinds_control",
        "backend.extensions.extension_builtin_job_kinds_integration",
        "backend.extensions.extension_builtin_job_kinds",
        "backend.extensions.extension_builtin_connector_kinds",
        "backend.extensions.extension_builtin_templates",
        "backend.extensions.extension_builtin_template_contracts",
    }
    allowlist = {
        "backend/extensions/extension_sdk.py",
        "backend/extensions/extension_contracts.py",
        "backend/extensions/extension_manifest_loader.py",
        "backend/extensions/extension_manifest_file_contracts.py",
        "backend/extensions/extension_manifest_parser.py",
        "backend/extensions/extension_manifest_projection.py",
        "backend/extensions/extension_manifest_schema_refs.py",
        "backend/extensions/extension_manifest_source.py",
        "backend/extensions/extension_registry.py",
        "backend/extensions/extension_registry_projection.py",
        "backend/extensions/extension_registry_bootstrap_writer.py",
        "backend/extensions/extension_registry_kind_writer.py",
        "backend/extensions/extension_registry_state.py",
        "backend/extensions/extension_registry_template_writer.py",
        "backend/extensions/extension_registry_writer.py",
        "backend/extensions/extension_bootstrap.py",
        "backend/extensions/extension_bootstrap_builtins.py",
        "backend/extensions/extension_bootstrap_state.py",
        "backend/extensions/extension_bootstrap_validation.py",
        "backend/extensions/extension_builtin_catalog.py",
        "backend/extensions/extension_builtin_kinds.py",
        "backend/extensions/extension_builtin_job_kinds_compute.py",
        "backend/extensions/extension_builtin_job_kinds_control.py",
        "backend/extensions/extension_builtin_job_kinds_integration.py",
        "backend/extensions/extension_builtin_job_kinds.py",
        "backend/extensions/extension_builtin_connector_kinds.py",
        "backend/extensions/extension_builtin_templates.py",
        "backend/extensions/extension_builtin_template_contracts.py",
        "backend/tests/unit/test_architecture_governance_gates.py",
    }

    violations: list[str] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        rel = _rel(path)
        if rel in allowlist:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in internal_modules:
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:{node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in internal_modules:
                        violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:{alias.name}")
    assert violations == []


def test_control_events_boundary_keeps_publish_contract_envelope_and_transport_out_of_publish_adapter() -> None:
    control_events_path = BACKEND_ROOT / "control_plane" / "adapters" / "control_events.py"
    contract_path = BACKEND_ROOT / "control_plane" / "adapters" / "control_event_contracts.py"
    envelope_path = BACKEND_ROOT / "control_plane" / "adapters" / "control_event_envelope.py"
    transport_path = BACKEND_ROOT / "control_plane" / "adapters" / "control_event_transport.py"

    source = control_events_path.read_text(encoding="utf-8-sig")
    contract_source = contract_path.read_text(encoding="utf-8-sig")
    envelope_source = envelope_path.read_text(encoding="utf-8-sig")
    transport_source = transport_path.read_text(encoding="utf-8-sig")

    assert "from .control_event_contracts import build_control_event_publish_contract" in source
    assert "from .control_event_envelope import build_control_event_message" in source
    assert "from .control_event_transport import publish_encoded_control_event" in source
    assert "control_plane_publish_subjects(" not in source
    assert "is_tenant_scoped_realtime_channel(" not in source
    assert "CONTROL_EVENT_ENVELOPE_RESERVED_FIELDS" not in source
    assert "uuid.uuid4()" not in source
    assert "time.time_ns()" not in source
    assert "event_bus.publish(" not in source
    assert "class ControlEventPublishContract" in contract_source
    assert "def build_control_event_publish_contract" in contract_source
    assert "def build_control_event_message" in envelope_source
    assert "def publish_encoded_control_event" in transport_source


def test_connectors_boundary_keeps_contract_endpoint_policy_and_tenant_queries_out_of_adapter() -> None:
    connectors_path = BACKEND_ROOT / "control_plane" / "adapters" / "connectors.py"
    contracts_path = BACKEND_ROOT / "control_plane" / "adapters" / "connectors_contracts.py"
    endpoint_policy_path = BACKEND_ROOT / "control_plane" / "adapters" / "connectors_endpoint_policy.py"
    queries_path = BACKEND_ROOT / "control_plane" / "adapters" / "connectors_queries.py"

    source = connectors_path.read_text(encoding="utf-8-sig")
    contracts_source = contracts_path.read_text(encoding="utf-8-sig")
    endpoint_policy_source = endpoint_policy_path.read_text(encoding="utf-8-sig")
    queries_source = queries_path.read_text(encoding="utf-8-sig")

    assert "from backend.control_plane.adapters.connectors_contracts import (" in source
    assert "from .connectors_endpoint_policy import validate_connector_endpoint" in source
    assert "from .connectors_queries import connector_stmt_for_tenant, load_connector_for_tenant" in source
    assert "class ConnectorUpsertRequest" not in source
    assert "class ConnectorResponse" not in source
    assert "def _validate_connector_endpoint" not in source
    assert "def _connector_stmt_for_tenant" not in source
    assert "urlparse(" not in source
    assert "ipaddress.ip_address(" not in source
    assert "class ConnectorUpsertRequest" in contracts_source
    assert "class ConnectorResponse" in contracts_source
    assert "def validate_connector_endpoint" in endpoint_policy_source
    assert "def connector_stmt_for_tenant" in queries_source
    assert "def load_connector_for_tenant" in queries_source


def test_trigger_service_boundary_keeps_fire_contract_queries_target_contracts_dispatch_and_delivery_runtime_out_of_orchestrator() -> None:
    trigger_service_path = BACKEND_ROOT / "extensions" / "trigger_service.py"
    fire_contract_path = BACKEND_ROOT / "extensions" / "trigger_fire_contract.py"
    delivery_queries_path = BACKEND_ROOT / "extensions" / "trigger_delivery_queries.py"
    target_contracts_path = BACKEND_ROOT / "extensions" / "trigger_target_contracts.py"
    target_validation_path = BACKEND_ROOT / "extensions" / "trigger_target_validation.py"
    target_dispatch_path = BACKEND_ROOT / "extensions" / "trigger_target_dispatch.py"
    delivery_runtime_path = BACKEND_ROOT / "extensions" / "trigger_delivery_runtime.py"

    source = trigger_service_path.read_text(encoding="utf-8-sig")
    fire_contract_source = fire_contract_path.read_text(encoding="utf-8-sig")
    delivery_queries_source = delivery_queries_path.read_text(encoding="utf-8-sig")
    target_contracts_source = target_contracts_path.read_text(encoding="utf-8-sig")
    target_validation_source = target_validation_path.read_text(encoding="utf-8-sig")
    target_dispatch_source = target_dispatch_path.read_text(encoding="utf-8-sig")
    delivery_runtime_source = delivery_runtime_path.read_text(encoding="utf-8-sig")

    assert "from .trigger_delivery_queries import delivery_definition_matches, get_delivery_by_idempotency_key" in source
    assert "from .trigger_delivery_runtime import mark_delivery_delivered_and_publish, mark_delivery_failed_and_publish" in source
    assert "from .trigger_fire_contract import normalize_trigger_fire_command" in source
    assert "from .trigger_target_dispatch import dispatch_trigger_target" in source
    assert "from .trigger_target_validation import validate_trigger_target_contract" in source
    assert "class JobTriggerTarget" not in source
    assert "class WorkflowTemplateTriggerTarget" not in source
    assert "def get_delivery_by_idempotency_key" not in source
    assert "def delivery_definition_matches" not in source
    assert "submit_job(" not in source
    assert "render_workflow_template(" not in source
    assert "create_workflow(" not in source
    assert "publish_control_event(" not in source
    assert "mark_delivery_failed(" not in source
    assert "mark_delivery_delivered(" not in source
    assert "class TriggerFireCommand" in fire_contract_source
    assert "def normalize_trigger_fire_command" in fire_contract_source
    assert "def get_delivery_by_idempotency_key" in delivery_queries_source
    assert "def delivery_definition_matches" in delivery_queries_source
    assert "class JobTriggerTarget" in target_contracts_source
    assert "def validate_trigger_target_contract" in target_validation_source
    assert "def dispatch_trigger_target" in target_dispatch_source
    assert "def build_delivery_event_payload" in delivery_runtime_source
    assert "def mark_delivery_failed_and_publish" in delivery_runtime_source
    assert "def mark_delivery_delivered_and_publish" in delivery_runtime_source


def test_workflows_boundary_keeps_contract_queries_projection_and_machine_callbacks_out_of_adapter() -> None:
    workflows_path = BACKEND_ROOT / "control_plane" / "adapters" / "workflows.py"
    contracts_path = BACKEND_ROOT / "control_plane" / "adapters" / "workflow_contracts.py"
    queries_path = BACKEND_ROOT / "control_plane" / "adapters" / "workflow_queries.py"
    projection_path = BACKEND_ROOT / "control_plane" / "adapters" / "workflow_projection.py"
    callbacks_path = BACKEND_ROOT / "control_plane" / "adapters" / "workflow_machine_callbacks.py"

    source = workflows_path.read_text(encoding="utf-8-sig")
    contracts_source = contracts_path.read_text(encoding="utf-8-sig")
    queries_source = queries_path.read_text(encoding="utf-8-sig")
    projection_source = projection_path.read_text(encoding="utf-8-sig")
    callbacks_source = callbacks_path.read_text(encoding="utf-8-sig")

    assert "from .workflow_contracts import (" in source
    assert "from .workflow_machine_callbacks import assert_machine_step_callback_contract" in source
    assert "from .workflow_projection import build_workflow_detail_response, workflow_to_response" in source
    assert "from .workflow_queries import list_workflow_steps, load_workflow_for_tenant, workflow_stmt_for_tenant" in source
    assert "class WorkflowStepDefinition" not in source
    assert "class WorkflowCreateRequest" not in source
    assert "class WorkflowStepCompleteRequest" not in source
    assert "select(WorkflowStep)" not in source
    assert "authenticate_node_request(" not in source
    assert "class WorkflowStepDefinition" in contracts_source
    assert "class WorkflowStepCompleteRequest" in contracts_source
    assert "def workflow_stmt_for_tenant" in queries_source
    assert "def load_workflow_for_tenant" in queries_source
    assert "def build_workflow_detail_response" in projection_source
    assert "def workflow_to_response" in projection_source
    assert "async def assert_machine_step_callback_contract" in callbacks_source


def test_extensions_adapter_uses_workflow_contracts_and_projection_instead_of_workflows_internals() -> None:
    extensions_path = BACKEND_ROOT / "control_plane" / "adapters" / "extensions.py"
    source = extensions_path.read_text(encoding="utf-8-sig")

    assert "from backend.control_plane.adapters.workflows import" not in source
    assert "from backend.control_plane.adapters.workflow_contracts import WorkflowDetailResponse" in source
    assert "from backend.control_plane.adapters.workflow_projection import build_workflow_detail_response" in source
    assert "from backend.control_plane.adapters.workflow_queries import list_workflow_steps" in source
    assert "StepStatus(" not in source
    assert "_to_response(" not in source


def test_orchestration_internal_modules_do_not_leak_past_public_boundaries() -> None:
    internal_modules = {
        "backend.control_plane.adapters.control_event_contracts",
        "backend.control_plane.adapters.control_event_envelope",
        "backend.control_plane.adapters.control_event_transport",
        "backend.control_plane.adapters.connectors_contracts",
        "backend.control_plane.adapters.connectors_endpoint_policy",
        "backend.control_plane.adapters.connectors_queries",
        "backend.control_plane.adapters.workflow_contracts",
        "backend.control_plane.adapters.workflow_machine_callbacks",
        "backend.control_plane.adapters.workflow_projection",
        "backend.control_plane.adapters.workflow_queries",
        "backend.extensions.trigger_delivery_queries",
        "backend.extensions.trigger_fire_contract",
        "backend.extensions.trigger_target_contracts",
        "backend.extensions.trigger_target_validation",
        "backend.extensions.trigger_target_dispatch",
        "backend.extensions.trigger_delivery_runtime",
    }
    allowlist = {
        "backend/control_plane/adapters/control_events.py",
        "backend/control_plane/adapters/control_event_contracts.py",
        "backend/control_plane/adapters/control_event_envelope.py",
        "backend/control_plane/adapters/control_event_transport.py",
        "backend/control_plane/adapters/connectors.py",
        "backend/control_plane/adapters/connectors_contracts.py",
        "backend/control_plane/adapters/connectors_endpoint_policy.py",
        "backend/control_plane/adapters/connectors_helpers.py",
        "backend/control_plane/adapters/connectors_queries.py",
        "backend/control_plane/adapters/extensions.py",
        "backend/control_plane/adapters/workflows.py",
        "backend/control_plane/adapters/workflow_contracts.py",
        "backend/control_plane/adapters/workflow_machine_callbacks.py",
        "backend/control_plane/adapters/workflow_projection.py",
        "backend/control_plane/adapters/workflow_queries.py",
        "backend/extensions/trigger_service.py",
        "backend/extensions/trigger_delivery_queries.py",
        "backend/extensions/trigger_target_contracts.py",
        "backend/extensions/trigger_target_validation.py",
        "backend/extensions/trigger_target_dispatch.py",
        "backend/extensions/trigger_delivery_runtime.py",
        "backend/extensions/trigger_fire_contract.py",
        "backend/tests/unit/test_architecture_governance_gates.py",
    }

    violations: list[str] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        rel = _rel(path)
        if rel in allowlist:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in internal_modules:
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:{node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in internal_modules:
                        violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:{alias.name}")
    assert violations == []


def test_topology_sentinel_boundary_routes_mount_and_switch_logic_through_runtime_planners() -> None:
    sentinel_path = BACKEND_ROOT / "sentinel" / "topology_sentinel.py"
    source = sentinel_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(sentinel_path))

    handle_mount = _function_def(tree, "_handle_mount")
    process_switch_event = _function_def(tree, "_process_switch_event_message")
    assert handle_mount is not None
    assert process_switch_event is not None
    assert _call_line(handle_mount, "resolve_debounced_mount_state") is not None
    assert _call_line(handle_mount, "plan_mount_state_transition") is not None
    assert _call_line(process_switch_event, "parse_switch_runtime_command") is not None
    assert _call_line(process_switch_event, "plan_switch_runtime_effects") is not None
    assert "SwitchCommandSignalPayload" not in source

    forbidden_calls = [
        ".".join(chain)
        for node in ast.walk(process_switch_event)
        if isinstance(node, ast.Call)
        for chain in (_expr_chain(node.func),)
        if chain[-2:] == ("json", "loads")
    ]
    assert forbidden_calls == []


def test_topology_sentinel_runtime_io_boundary_routes_redis_and_event_bus_side_effects_through_runtime_io() -> None:
    sentinel_path = BACKEND_ROOT / "sentinel" / "topology_sentinel.py"
    runtime_io_path = BACKEND_ROOT / "sentinel" / "topology_runtime_io.py"
    source = sentinel_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(sentinel_path))

    connect_redis = _function_def(tree, "_connect_redis")
    publish_disk_taint = _function_def(tree, "_publish_disk_taint")
    update_state = _function_def(tree, "_update_state")
    probe_gpu = _function_def(tree, "_probe_gpu")
    listener_thread = _function_def(tree, "_redis_listener_thread")
    assert connect_redis is not None
    assert publish_disk_taint is not None
    assert update_state is not None
    assert probe_gpu is not None
    assert listener_thread is not None

    assert "from backend.sentinel.topology_runtime_io import TopologyRuntimeIO" in source
    assert "def _set_event_publisher" not in source
    assert "def _publish_control_event" not in source
    assert "def _publish_internal_signal" not in source
    assert _call_line(connect_redis, "replace_redis") is not None
    assert _call_line(publish_disk_taint, "publish_signal") is not None
    assert _call_line(publish_disk_taint, "set_disk_taint") is not None
    assert _call_line(update_state, "write_mount_state") is not None
    assert _call_line(probe_gpu, "write_gpu_state") is not None
    assert _call_line(listener_thread, "subscribe_switch_commands") is not None
    assert runtime_io_path.exists()


def test_event_channel_contract_separates_browser_realtime_from_internal_coordination() -> None:
    contract = export_event_channel_contract()
    control_plane = set(contract["control_plane_event_channels"])
    browser_realtime = set(contract["browser_realtime_event_channels"])
    browser_public = set(contract["browser_public_realtime_event_channels"])
    tenant_scoped = set(contract["tenant_scoped_realtime_event_channels"])
    internal = set(contract["internal_coordination_channels"])
    envelope_contract = contract["control_event_envelope_contract"]
    tenant_subject_contract = contract["tenant_realtime_subject_contract"]

    assert control_plane
    assert browser_realtime
    assert browser_public
    assert internal
    assert envelope_contract["publisher_entrypoint"] == "backend.control_plane.adapters.control_events.publish_control_event"
    assert envelope_contract["reserved_fields"] == ["event_id", "revision", "action", "ts", "tenant_id"]
    assert envelope_contract["tenant_scoped_channels_require_tenant_id"] is True
    assert "session:events" in control_plane
    assert "session:events" in browser_realtime
    assert "session:events" in tenant_scoped
    assert "user:events" in control_plane
    assert "user:events" in browser_realtime
    assert "user:events" in tenant_scoped
    assert browser_realtime <= control_plane
    assert control_plane.isdisjoint(internal)
    assert browser_public == browser_realtime - tenant_scoped
    assert tenant_subject_contract["segment"] == "tenant"
    assert tenant_subject_contract["tenant_id_encoding"] == "utf8-hex"
    assert tenant_realtime_subject("job:events", "tenant-a") == f"job:events.tenant.{tenant_subject_token('tenant-a')}"


def test_event_transport_gate_blocks_direct_pubsub_usage_outside_event_interfaces() -> None:
    allowlist = {
        "backend/platform/events/publisher.py",
        "backend/platform/events/redis_bus.py",
        "backend/platform/events/subscriber.py",
    }
    violations: list[str] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        rel = _rel(path)
        if rel.startswith("backend/tests/") or rel in allowlist:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _expr_chain(node.func)
            if chain[-2:] == ("pubsub", "publish") or chain[-2:] == ("pubsub", "session"):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:{'.'.join(chain[-2:])}")
    assert violations == []


def test_control_event_gate_blocks_reserved_envelope_key_overrides_in_publishers() -> None:
    reserved_fields = set(export_event_channel_contract()["control_event_envelope_contract"]["reserved_fields"])
    violations: list[str] = []
    for path in _python_sources("control_plane", "extensions"):
        rel = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _expr_chain(node.func)
            if not chain or chain[-1] != "publish_control_event":
                continue
            payload_node: ast.AST | None = None
            if len(node.args) >= 3:
                payload_node = node.args[2]
            else:
                for keyword in node.keywords:
                    if keyword.arg == "payload":
                        payload_node = keyword.value
                        break
            literal_keys = _dict_literal_string_keys(payload_node)
            overridden = sorted(literal_keys & reserved_fields)
            if overridden:
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:{overridden}")
    assert violations == []


def test_runtime_state_contract_is_ephemeral_and_non_authoritative() -> None:
    contract = export_runtime_state_contract()
    runtime_state = contract["redis_ephemeral_runtime_state"]

    assert contract["authoritative_redis_runtime_state_allowed"] is False
    assert runtime_state
    assert all(entry["authoritative"] is False for entry in runtime_state)
    assert all(str(entry["pattern"]).strip() for entry in runtime_state)
    assert all(not str(entry["pattern"]).startswith("switch_expected:") for entry in runtime_state)


def test_runtime_contract_taxonomy_exports_persona_executor_and_workload_layers() -> None:
    contract = export_runtime_contract_taxonomy()

    personas = contract["control_plane_personas"]
    persona_defaults = contract["persona_to_default_executor_contract"]
    canonical_executor_contracts = contract["canonical_executor_contracts"]
    workload_kinds = set(contract["workload_kinds"])
    authority_boundaries = contract["runtime_authority_boundaries"]

    assert personas
    assert canonical_executor_contracts
    assert workload_kinds
    assert authority_boundaries
    assert {item["key"] for item in personas} == set(control_plane_persona_keys())
    assert set(persona_defaults) == {item["key"] for item in personas}
    assert {item["layer"] for item in authority_boundaries} == {"persona", "executor_contract", "workload_kind"}
    for executor_name, executor_contract in canonical_executor_contracts.items():
        assert executor_name
        assert set(executor_contract["supported_workload_kinds"]) <= workload_kinds


def test_runtime_contract_gate_blocks_hidden_persona_literals_in_scheduler_paths() -> None:
    risky_modules = (
        BACKEND_ROOT / "control_plane" / "adapters" / "nodes_helpers.py",
        BACKEND_ROOT / "runtime" / "scheduling" / "job_scheduler.py",
        BACKEND_ROOT / "runtime" / "scheduling" / "scheduling_candidates.py",
        BACKEND_ROOT / "runtime" / "scheduling" / "job_scoring.py",
    )
    persona_literals = {value for value in control_plane_persona_keys() if value != "unknown"}
    violations: list[str] = []
    for path in risky_modules:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        literals = {
            node.value.strip()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip() in persona_literals
        }
        if literals:
            violations.append(f"{_rel(path)}:{sorted(literals)}")
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
    for path in _python_sources(*SCANNED_SOURCE_FOLDERS):
        rel = _rel(path)
        source = path.read_text(encoding="utf-8")
        for module in blocked:
            if module in source:
                violations.append(f"{rel}:{module}")
    assert violations == []


def test_platform_redis_gate_blocks_sdk_imports_outside_platform() -> None:
    violations: list[str] = []
    for path in _python_sources(*SCANNED_SOURCE_FOLDERS):
        rel = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "redis" or alias.name.startswith("redis.") for alias in node.names):
                    violations.append(f"{rel}:{getattr(node, 'lineno', 0)}")
            elif isinstance(node, ast.ImportFrom) and (node.module == "redis" or (node.module or "").startswith("redis.")):
                violations.append(f"{rel}:{getattr(node, 'lineno', 0)}")
    assert violations == []


def test_platform_redis_gate_blocks_client_escape_hatch_usage() -> None:
    violations: list[str] = []
    for path in _python_sources(*SCANNED_SOURCE_FOLDERS):
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
    for path in _python_sources(*SCANNED_SOURCE_FOLDERS):
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
        "backend.kernel.contracts.role_claims",
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
    for path in _python_sources("control_plane", "core", "kernel", "runtime", "extensions", "workers"):
        rel = _rel(path)
        for lineno, pair in _assignment_pairs(path):
            allowed = _OWNER_MODULES_BY_FIELD[pair]
            if rel not in allowed:
                violations.append(f"{rel}:{lineno}:{pair[0]}.{pair[1]}")
    assert violations == []


def test_lease_gate_only_allows_lease_service_writes() -> None:
    violations: list[str] = []
    for path in _python_sources("control_plane", "core", "kernel", "runtime", "extensions", "workers"):
        rel = _rel(path)
        for lineno, pair in _assignment_pairs(path):
            if pair not in _LEASE_ONLY_FIELDS:
                continue
            if rel != "backend/runtime/execution/lease_service.py":
                violations.append(f"{rel}:{lineno}:{pair[0]}.{pair[1]}")
    assert violations == []


def test_runtime_policy_contract_exports_policy_store_entrypoint() -> None:
    contract = export_runtime_policy_contract()

    assert contract["entrypoint"] == "backend.kernel.policy.runtime_policy_resolver.RuntimePolicyResolver"
    assert contract["policy_store_entrypoint"] == "backend.kernel.policy.policy_store.get_policy_store"
    assert contract["profile_normalizer"] == "backend.kernel.profiles.public_profile.normalize_gateway_profile"
    assert contract["runtime_pack_resolver"] == "backend.runtime.topology.profile_selection.resolve_runtime_pack_keys"
    assert contract["router_gate_method"] == "router_enabled"
    assert contract["snapshot_method"] == "snapshot"


def test_lease_service_contract_exports_owned_fields_and_rotation_semantics() -> None:
    contract = export_lease_service_contract()

    assert contract["entrypoint"] == "backend.runtime.execution.lease_service.LeaseService"
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

    assert tuple(rules.keys()) == tuple(f"A{i}" for i in range(1, 19))
    assert rules["A1"]["maturity"] == "enforced"
    assert rules["A6"]["maturity"] == "enforced"
    assert rules["A12"]["maturity"] == "enforced"
    assert rules["A14"]["maturity"] == "enforced"
    assert rules["A15"]["maturity"] == "enforced"
    assert rules["A16"]["maturity"] == "enforced"
    assert rules["A17"]["maturity"] == "enforced"
    assert rules["A18"]["maturity"] == "enforced"
    assert "surface_registry" in snapshot["entrypoints"]
    assert snapshot["entrypoints"]["aggregate_owner_registry"] == "backend.kernel.governance.aggregate_owner_registry.export_aggregate_owner_registry"
    assert snapshot["entrypoints"]["event_channel_contract"] == "backend.platform.events.channels.export_event_channel_contract"
    assert snapshot["entrypoints"]["runtime_state_contract"] == "backend.platform.redis.runtime_state.export_runtime_state_contract"
    assert snapshot["entrypoints"]["domain_import_fence"] == "backend.kernel.governance.domain_import_fence.export_backend_domain_import_fence"
    assert snapshot["entrypoints"]["auth_boundary_contract"] == "backend.control_plane.auth.authority_boundary.export_auth_boundary_contract"
    assert snapshot["entrypoints"]["development_cleanroom_contract"] == "backend.kernel.governance.development_cleanroom.export_development_cleanroom_contract"
    assert snapshot["registries"]["surface_registry"] == export_surface_registry()
    assert snapshot["registries"]["fault_isolation_contract"] == export_fault_isolation_contract()
    assert snapshot["registries"]["aggregate_owner_registry"] == export_aggregate_owner_registry()
    assert snapshot["registries"]["status_compatibility_rules"] == export_status_compatibility_rules()
    assert snapshot["registries"]["event_channel_contract"] == export_event_channel_contract()
    assert snapshot["registries"]["runtime_state_contract"] == export_runtime_state_contract()
    assert snapshot["registries"]["domain_import_fence"] == export_backend_domain_import_fence()
    assert snapshot["registries"]["auth_boundary_contract"] == export_auth_boundary_contract()
    assert snapshot["registries"]["development_cleanroom_contract"] == export_development_cleanroom_contract()


def test_domain_import_fence_contract_is_code_backed_and_repo_governed() -> None:
    contract = export_backend_domain_import_fence()

    assert contract["governed_domains"] == ["kernel", "control_plane", "runtime", "extensions", "platform"]
    assert contract["allowlists"]["kernel_to_control_plane"] == [
        "backend/kernel/governance/architecture_rules.py",
    ]
    assert contract["allowlists"]["kernel_to_runtime"] == [
        "backend/kernel/governance/architecture_rules.py",
        "backend/kernel/policy/runtime_policy_resolver.py",
    ]
    assert contract["allowlists"]["runtime_to_control_plane"] == [
        "backend/runtime/topology/node_enrollment_service.py",
    ]
    assert contract["platform_kernel_contract_prefix"] == "backend.kernel.contracts."


def test_auth_boundary_contract_exports_authoritative_entrypoints() -> None:
    contract = export_auth_boundary_contract()

    assert contract["role_claim_contract"]["entrypoint"] == "backend.kernel.contracts.role_claims.current_user_role"
    assert contract["role_claim_contract"]["allowlist"] == ["backend/kernel/contracts/role_claims.py"]
    assert contract["tenant_claim_contract"]["entrypoints"] == [
        "backend.kernel.contracts.tenant_claims.current_user_tenant_id",
        "backend.kernel.contracts.tenant_claims.require_current_user_tenant_id",
    ]
    assert contract["tenant_claim_contract"]["allowlist"] == ["backend/kernel/contracts/tenant_claims.py"]
    assert contract["tenant_claim_contract"]["forbidden_direct_patterns"] == [
        'current_user.get("tenant_id")',
        "current_user.get('tenant_id')",
        'current_user["tenant_id"]',
        "current_user['tenant_id']",
        '(current_user or {}).get("tenant_id")',
        "(current_user or {}).get('tenant_id')",
        '(current_user or {})["tenant_id"]',
        "(current_user or {})['tenant_id']",
    ]
    assert contract["admin_policy_contract"]["module"] == "backend.control_plane.auth.access_policy"
    assert contract["permission_scope_contract"]["module"] == "backend.control_plane.auth.permissions"
    assert contract["tenant_context_contract"]["jwt_tenant_db_entrypoint"] == "backend.control_plane.adapters.deps.get_tenant_db"
    assert contract["tenant_context_contract"]["machine_tenant_db_entrypoint"] == "backend.control_plane.adapters.deps.get_machine_tenant_db"
    assert contract["audit_log_contract"]["entrypoint"] == "backend.platform.logging.audit.log_audit"
    assert contract["audit_log_contract"]["helper_allowlist"] == ["backend/platform/logging/audit.py"]
    assert contract["audit_log_contract"]["forbidden_direct_helpers"] == [
        "extract_client_info",
        "sanitize_audit_details",
        "write_audit_log",
    ]
    assert contract["cookie_policy_contract"]["entrypoints"] == [
        "backend.control_plane.adapters.auth_cookies.get_auth_cookie_token",
        "backend.control_plane.adapters.auth_cookies.set_auth_cookie",
        "backend.control_plane.adapters.auth_cookies.clear_auth_cookie",
        "backend.control_plane.auth.webauthn_flow_session.ensure_webauthn_flow_session",
        "backend.control_plane.auth.webauthn_flow_session.require_webauthn_flow_session",
        "backend.control_plane.auth.webauthn_flow_session.clear_webauthn_flow_session",
    ]
    assert contract["cookie_policy_contract"]["raw_cookie_allowlist"] == ["backend/control_plane/auth/cookie_policy.py"]
    assert contract["cookie_policy_contract"]["forbidden_direct_patterns"] == [
        "request.cookies",
        "response.set_cookie(",
        "response.delete_cookie(",
    ]
    assert contract["auth_request_tenant_contract"]["entrypoint"] == "backend.control_plane.adapters.auth_shared.request_tenant_id"
    assert contract["auth_request_tenant_contract"]["request_models"] == [
        "backend.control_plane.adapters.models.auth.PasswordLoginRequest",
        "backend.control_plane.adapters.models.auth.PinLoginRequest",
        "backend.control_plane.adapters.models.auth.WebAuthnRegisterBeginRequest",
        "backend.control_plane.adapters.models.auth.WebAuthnLoginBeginRequest",
        "backend.control_plane.adapters.models.auth.WebAuthnLoginCompleteRequest",
    ]
    assert contract["auth_request_tenant_contract"]["tenant_scoped_admin_entrypoints"] == [
        "backend.control_plane.adapters.auth_shared.bind_admin_scope",
        "backend.control_plane.adapters.auth_shared.enforce_admin_scope",
    ]
    assert contract["auth_request_tenant_contract"]["token_validation_entrypoints"] == [
        "backend.control_plane.auth.subject_authority.assert_token_subject_active",
        "backend.control_plane.auth.sessions.validate_session_claims",
    ]
    assert contract["auth_request_tenant_contract"]["default_tenant_fallback_allowed"] is False
    assert contract["auth_actor_contract"] == {
        "module": "backend.control_plane.adapters.auth_shared",
        "actor_entrypoints": [
            "backend.control_plane.adapters.auth_shared.resolve_auth_actor",
            "backend.control_plane.adapters.auth_shared.build_auth_actor_payload",
        ],
        "cookie_scope_entrypoint": "backend.control_plane.adapters.auth_shared.should_clear_auth_cookie_for_self_target",
        "adapters": [
            "backend.control_plane.adapters.auth_pin",
            "backend.control_plane.adapters.auth_user",
            "backend.control_plane.adapters.permissions",
            "backend.control_plane.adapters.sessions",
            "backend.control_plane.adapters.user_management",
        ],
    }
    assert contract["session_authority_contract"] == {
        "module": "backend.control_plane.auth.sessions",
        "token_validation_entrypoint": "backend.control_plane.auth.sessions.validate_session_claims",
        "token_rotation_entrypoint": "backend.control_plane.auth.jwt.decode_token",
        "self_revoke_entrypoint": "backend.control_plane.auth.sessions.revoke_owned_session",
        "tenant_bulk_revoke_entrypoint": "backend.control_plane.auth.sessions.revoke_all_user_sessions",
        "browser_self_logout_entrypoints": [
            "backend.control_plane.adapters.sessions.revoke_my_session",
            "backend.control_plane.adapters.sessions.revoke_all_my_sessions",
        ],
        "cookie_clear_entrypoint": "backend.control_plane.adapters.auth_cookies.clear_auth_cookie",
        "cookie_clear_helper": "backend.control_plane.adapters.sessions._clear_auth_cookie_for_self_session_mutation",
        "self_service_lookup_fields": ["tenant_id", "user_id", "session_id"],
        "session_backed_rotation_requires_authority": True,
        "stateless_legacy_rotation_allowed": True,
        "current_session_revoke_clears_auth_cookie": True,
        "bulk_self_revoke_clears_auth_cookie": True,
        "forbidden_self_service_entrypoints": [
            "backend.control_plane.auth.sessions.revoke_session",
        ],
    }
    assert contract["session_mutation_contract"] == {
        "adapter_module": "backend.control_plane.adapters.sessions",
        "single_session_invalidation_entrypoint": "backend.control_plane.auth.sessions.revoke_owned_session",
        "bulk_session_invalidation_entrypoint": "backend.control_plane.auth.sessions.revoke_all_user_sessions",
        "entrypoints": [
            "backend.control_plane.adapters.sessions.revoke_my_session",
            "backend.control_plane.adapters.sessions.revoke_all_my_sessions",
            "backend.control_plane.adapters.sessions.revoke_all_user_sessions_admin",
        ],
        "actions": ["session_revoked", "sessions_revoked", "user_sessions_revoked"],
        "audit_helper": "backend.control_plane.adapters.sessions._record_session_mutation_audit",
        "audit_entrypoint": "backend.platform.logging.audit.log_audit",
        "event_helper": "backend.control_plane.adapters.sessions._publish_session_mutation_event",
        "event_entrypoint": "backend.control_plane.adapters.control_events.publish_control_event",
        "event_channel": "session:events",
        "cookie_clear_helper": "backend.control_plane.adapters.sessions._clear_auth_cookie_for_self_session_mutation",
        "commit_before_publish": True,
        "invalidate_sessions_before_commit": True,
    }
    assert contract["user_lifecycle_contract"] == {
        "adapter_module": "backend.control_plane.adapters.user_management",
        "service_module": "backend.control_plane.admin.user_lifecycle",
        "actions": ["suspended", "activated", "deleted"],
        "audit_helper": "backend.control_plane.adapters.user_management._record_user_lifecycle_audit",
        "audit_entrypoint": "backend.platform.logging.audit.log_audit",
        "event_helper": "backend.control_plane.adapters.user_management._publish_user_lifecycle_event",
        "event_entrypoint": "backend.control_plane.adapters.control_events.publish_control_event",
        "event_channel": "user:events",
        "commit_before_publish": True,
    }
    assert contract["user_provisioning_contract"] == {
        "adapter_module": "backend.control_plane.adapters.auth_user",
        "entrypoint": "backend.control_plane.adapters.auth_user.create_user",
        "admin_scope_binding_entrypoint": "backend.control_plane.adapters.auth_shared.bind_admin_scope",
        "admin_scope_enforcement_entrypoint": "backend.control_plane.adapters.auth_shared.enforce_admin_scope",
        "audit_helper": "backend.control_plane.adapters.auth_user._record_user_provisioning_audit",
        "audit_entrypoint": "backend.platform.logging.audit.log_audit",
        "event_helper": "backend.control_plane.adapters.auth_user._publish_user_provisioning_event",
        "event_entrypoint": "backend.control_plane.adapters.control_events.publish_control_event",
        "event_channel": "user:events",
        "event_action": "user_created",
        "commit_before_publish": True,
    }
    assert contract["permission_mutation_contract"] == {
        "adapter_module": "backend.control_plane.adapters.permissions",
        "service_module": "backend.control_plane.auth.permissions",
        "actions": ["permission_granted", "permission_revoked"],
        "session_invalidation_entrypoint": "backend.control_plane.auth.sessions.revoke_all_user_sessions",
        "audit_helper": "backend.control_plane.adapters.permissions._record_permission_mutation_audit",
        "audit_entrypoint": "backend.platform.logging.audit.log_audit",
        "event_helper": "backend.control_plane.adapters.permissions._publish_permission_mutation_event",
        "event_entrypoint": "backend.control_plane.adapters.control_events.publish_control_event",
        "event_channel": "user:events",
        "self_target_cookie_clear_helper": "backend.control_plane.adapters.permissions._clear_auth_cookie_for_self_permission_mutation",
        "commit_before_publish": True,
        "invalidate_sessions_before_commit": True,
    }
    assert contract["credential_mutation_contract"] == {
        "session_invalidation_entrypoint": "backend.control_plane.auth.sessions.revoke_all_user_sessions",
        "audit_entrypoint": "backend.platform.logging.audit.log_audit",
        "event_entrypoint": "backend.control_plane.adapters.control_events.publish_control_event",
        "event_channel": "user:events",
        "cookie_clear_entrypoint": "backend.control_plane.adapters.auth_cookies.clear_auth_cookie",
        "pin_mutation": {
            "adapter_module": "backend.control_plane.adapters.auth_pin",
            "entrypoint": "backend.control_plane.adapters.auth_pin.pin_set",
            "audit_helper": "backend.control_plane.adapters.auth_pin._record_pin_mutation_audit",
            "event_helper": "backend.control_plane.adapters.auth_pin._publish_pin_mutation_event",
            "cookie_clear_helper": "backend.control_plane.adapters.auth_pin._clear_auth_cookie_after_pin_mutation",
            "event_action": "pin_updated",
        },
        "credential_revocation": {
            "adapter_module": "backend.control_plane.adapters.auth_user",
            "entrypoint": "backend.control_plane.adapters.auth_user.revoke_credential",
            "audit_helper": "backend.control_plane.adapters.auth_user._record_webauthn_credential_revocation_audit",
            "event_helper": "backend.control_plane.adapters.auth_user._publish_webauthn_credential_revocation_event",
            "cookie_clear_helper": "backend.control_plane.adapters.auth_user._clear_auth_cookie_for_self_credential_revocation",
            "event_action": "webauthn_credential_revoked",
        },
        "commit_before_publish": True,
        "invalidate_sessions_before_commit": True,
    }
    assert contract["adapter_dependency_contract"] == {
        "dynamic_auth_module_lookup_allowed": False,
        "forbidden_patterns": [
            'sys.modules.get("backend.control_plane.adapters.auth")',
            '__import__("backend.control_plane.adapters.auth"',
            "def _auth_mod",
        ],
        "explicit_patch_surfaces": [
            "backend.control_plane.adapters.auth_shared.set_tenant_context",
            "backend.control_plane.adapters.auth_token_issue.token_response",
            "backend.control_plane.adapters.auth_webauthn.check_webauthn_rate_limit",
            "backend.control_plane.adapters.auth_webauthn.generate_authentication_challenge",
            "backend.control_plane.adapters.auth_webauthn.credential_id_to_base64url",
            "backend.control_plane.adapters.auth_webauthn.expected_challenge_bytes",
            "backend.control_plane.adapters.auth_webauthn.origin_from_request",
            "backend.control_plane.adapters.auth_webauthn.verify_authentication",
        ],
    }


def test_auth_boundary_gate_blocks_dynamic_auth_adapter_reflection() -> None:
    contract = export_auth_boundary_contract()["adapter_dependency_contract"]
    guarded_paths = (
        BACKEND_ROOT / "control_plane" / "adapters" / "auth_shared.py",
        BACKEND_ROOT / "control_plane" / "adapters" / "auth_token_issue.py",
        BACKEND_ROOT / "control_plane" / "adapters" / "auth_webauthn.py",
    )
    violations: list[str] = []
    for path in guarded_paths:
        source = path.read_text(encoding="utf-8")
        for pattern in contract["forbidden_patterns"]:
            if pattern in source:
                violations.append(f"{_rel(path)}:{pattern}")
    assert violations == []


def test_auth_mutation_adapters_delegate_actor_projection_to_auth_shared() -> None:
    contract = export_auth_boundary_contract()["auth_actor_contract"]
    scenarios = {
        "backend/control_plane/adapters/auth_pin.py": {
            "imports": {"build_auth_actor_payload", "resolve_auth_actor"},
            "calls": {"build_auth_actor_payload", "resolve_auth_actor"},
            "forbidden_defs": {"_current_user_id"},
        },
        "backend/control_plane/adapters/auth_user.py": {
            "imports": {"build_auth_actor_payload", "resolve_auth_actor", "should_clear_auth_cookie_for_self_target"},
            "calls": {"build_auth_actor_payload", "resolve_auth_actor", "should_clear_auth_cookie_for_self_target"},
            "forbidden_defs": {"_current_user_id"},
        },
        "backend/control_plane/adapters/permissions.py": {
            "imports": {"build_auth_actor_payload", "resolve_auth_actor", "should_clear_auth_cookie_for_self_target"},
            "calls": {"build_auth_actor_payload", "resolve_auth_actor", "should_clear_auth_cookie_for_self_target"},
            "forbidden_defs": {"_current_user_id"},
        },
        "backend/control_plane/adapters/sessions.py": {
            "imports": {"build_auth_actor_payload", "resolve_auth_actor", "should_clear_auth_cookie_for_self_target"},
            "calls": {"build_auth_actor_payload", "resolve_auth_actor", "should_clear_auth_cookie_for_self_target"},
            "forbidden_defs": {"_current_user_id", "_current_session_id"},
        },
        "backend/control_plane/adapters/user_management.py": {
            "imports": {"build_auth_actor_payload", "resolve_auth_actor"},
            "calls": {"build_auth_actor_payload", "resolve_auth_actor"},
            "forbidden_defs": set(),
        },
    }

    violations: list[str] = []
    for rel_path, requirements in scenarios.items():
        path = ROOT / rel_path
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == contract["module"]
            for alias in node.names
        }
        defined_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        }
        called_names = {
            chain[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for chain in [_expr_chain(node.func)]
            if chain
        }

        for import_name in sorted(requirements["imports"] - imported_names):
            violations.append(f"{rel_path}:missing_import:{import_name}")
        for call_name in sorted(requirements["calls"] - called_names):
            violations.append(f"{rel_path}:missing_call:{call_name}")
        for helper_name in sorted(requirements["forbidden_defs"] & defined_names):
            violations.append(f"{rel_path}:local_helper:{helper_name}")

    assert violations == []


def test_session_authority_gate_blocks_generic_session_revocation_from_adapters() -> None:
    forbidden_entrypoints = set(export_auth_boundary_contract()["session_authority_contract"]["forbidden_self_service_entrypoints"])
    violations: list[str] = []
    for path in _python_sources("control_plane"):
        rel = _rel(path)
        if rel == "backend/control_plane/auth/sessions.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _expr_chain(node.func)
            if not chain or chain[-1] != "revoke_session":
                continue
            violations.append(f"{rel}:{getattr(node, 'lineno', 0)}:{next(iter(forbidden_entrypoints))}")
    assert violations == []


def test_session_authority_gate_requires_cookie_clear_for_browser_self_logout_paths() -> None:
    contract = export_auth_boundary_contract()["session_authority_contract"]
    adapter_path = BACKEND_ROOT / "control_plane" / "adapters" / "sessions.py"
    tree = ast.parse(adapter_path.read_text(encoding="utf-8-sig"), filename=str(adapter_path))
    required_entrypoints = contract["browser_self_logout_entrypoints"]
    cookie_clear_helper = contract["cookie_clear_helper"]
    violations: list[str] = []
    for entrypoint in required_entrypoints:
        function_name = entrypoint.rsplit(".", 1)[-1]
        function_def = _function_def(tree, function_name)
        if function_def is None:
            violations.append(f"{_rel(adapter_path)}:missing:{function_name}")
            continue
        helper_line = _call_line(function_def, cookie_clear_helper.rsplit(".", 1)[-1])
        if helper_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{cookie_clear_helper}")
    helper_def = _function_def(tree, cookie_clear_helper.rsplit(".", 1)[-1])
    if helper_def is None or _call_line(helper_def, "clear_auth_cookie") is None:
        violations.append(f"{_rel(adapter_path)}:{cookie_clear_helper}:{contract['cookie_clear_entrypoint']}")
    assert violations == []


def test_user_lifecycle_gate_requires_audit_commit_and_publish_order() -> None:
    contract = export_auth_boundary_contract()["user_lifecycle_contract"]
    adapter_path = BACKEND_ROOT / "control_plane" / "adapters" / "user_management.py"
    tree = ast.parse(adapter_path.read_text(encoding="utf-8-sig"), filename=str(adapter_path))
    source = adapter_path.read_text(encoding="utf-8-sig")
    function_names = (
        "suspend_user_endpoint",
        "activate_user_endpoint",
        "delete_user_endpoint",
    )
    violations: list[str] = []
    for function_name in function_names:
        function_def = _function_def(tree, function_name)
        if function_def is None:
            violations.append(f"{_rel(adapter_path)}:missing:{function_name}")
            continue
        audit_line = _call_line(function_def, contract["audit_helper"].rsplit(".", 1)[-1])
        commit_line = _call_line(function_def, "commit")
        publish_line = _call_line(function_def, contract["event_helper"].rsplit(".", 1)[-1])
        if audit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['audit_helper']}")
            continue
        if commit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:db.commit")
            continue
        if publish_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['event_helper']}")
            continue
        if not (audit_line < commit_line < publish_line):
            violations.append(f"{_rel(adapter_path)}:{function_name}:order")
    audit_helper_def = _function_def(tree, contract["audit_helper"].rsplit(".", 1)[-1])
    if audit_helper_def is None or _call_line(audit_helper_def, "log_audit") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['audit_helper']}:{contract['audit_entrypoint']}")
    event_helper_def = _function_def(tree, contract["event_helper"].rsplit(".", 1)[-1])
    if event_helper_def is None or _call_line(event_helper_def, "publish_control_event") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_entrypoint']}")
    if "CHANNEL_USER_EVENTS" not in source:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_channel']}")
    assert violations == []


def test_user_provisioning_gate_requires_scope_audit_commit_and_publish_order() -> None:
    contract = export_auth_boundary_contract()["user_provisioning_contract"]
    adapter_path = BACKEND_ROOT / "control_plane" / "adapters" / "auth_user.py"
    tree = ast.parse(adapter_path.read_text(encoding="utf-8-sig"), filename=str(adapter_path))
    source = adapter_path.read_text(encoding="utf-8-sig")
    function_name = contract["entrypoint"].rsplit(".", 1)[-1]
    function_def = _function_def(tree, function_name)
    violations: list[str] = []
    if function_def is None:
        violations.append(f"{_rel(adapter_path)}:missing:{function_name}")
    else:
        bind_scope_line = _call_line(function_def, contract["admin_scope_binding_entrypoint"].rsplit(".", 1)[-1])
        enforce_scope_line = _call_line(function_def, contract["admin_scope_enforcement_entrypoint"].rsplit(".", 1)[-1])
        audit_line = _call_line(function_def, contract["audit_helper"].rsplit(".", 1)[-1])
        commit_line = _call_line(function_def, "commit")
        publish_line = _call_line(function_def, contract["event_helper"].rsplit(".", 1)[-1])
        if bind_scope_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['admin_scope_binding_entrypoint']}")
        if enforce_scope_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['admin_scope_enforcement_entrypoint']}")
        if audit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['audit_helper']}")
        if commit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:db.commit")
        if publish_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['event_helper']}")
        if (
            bind_scope_line is not None
            and enforce_scope_line is not None
            and audit_line is not None
            and commit_line is not None
            and publish_line is not None
            and not (bind_scope_line <= enforce_scope_line < audit_line < commit_line < publish_line)
        ):
            violations.append(f"{_rel(adapter_path)}:{function_name}:order")
    audit_helper_def = _function_def(tree, contract["audit_helper"].rsplit(".", 1)[-1])
    if audit_helper_def is None or _call_line(audit_helper_def, "log_audit") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['audit_helper']}:{contract['audit_entrypoint']}")
    event_helper_def = _function_def(tree, contract["event_helper"].rsplit(".", 1)[-1])
    if event_helper_def is None or _call_line(event_helper_def, "publish_control_event") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_entrypoint']}")
    if "CHANNEL_USER_EVENTS" not in source:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_channel']}")
    assert violations == []


def test_permission_mutation_gate_requires_invalidation_audit_commit_and_publish_order() -> None:
    contract = export_auth_boundary_contract()["permission_mutation_contract"]
    adapter_path = BACKEND_ROOT / "control_plane" / "adapters" / "permissions.py"
    tree = ast.parse(adapter_path.read_text(encoding="utf-8-sig"), filename=str(adapter_path))
    source = adapter_path.read_text(encoding="utf-8-sig")
    function_names = (
        "grant_permission_endpoint",
        "revoke_permission_endpoint",
    )
    violations: list[str] = []
    for function_name in function_names:
        function_def = _function_def(tree, function_name)
        if function_def is None:
            violations.append(f"{_rel(adapter_path)}:missing:{function_name}")
            continue
        invalidate_line = _call_line(function_def, "revoke_all_user_sessions")
        audit_line = _call_line(function_def, contract["audit_helper"].rsplit(".", 1)[-1])
        commit_line = _call_line(function_def, "commit")
        publish_line = _call_line(function_def, contract["event_helper"].rsplit(".", 1)[-1])
        clear_cookie_line = _call_line(function_def, contract["self_target_cookie_clear_helper"].rsplit(".", 1)[-1])
        if invalidate_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['session_invalidation_entrypoint']}")
            continue
        if audit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['audit_helper']}")
            continue
        if commit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:db.commit")
            continue
        if publish_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['event_helper']}")
            continue
        if clear_cookie_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['self_target_cookie_clear_helper']}")
            continue
        if not (invalidate_line < audit_line < commit_line < clear_cookie_line < publish_line):
            violations.append(f"{_rel(adapter_path)}:{function_name}:order")
    audit_helper_def = _function_def(tree, contract["audit_helper"].rsplit(".", 1)[-1])
    if audit_helper_def is None or _call_line(audit_helper_def, "log_audit") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['audit_helper']}:{contract['audit_entrypoint']}")
    event_helper_def = _function_def(tree, contract["event_helper"].rsplit(".", 1)[-1])
    if event_helper_def is None or _call_line(event_helper_def, "publish_control_event") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_entrypoint']}")
    clear_cookie_helper_def = _function_def(tree, contract["self_target_cookie_clear_helper"].rsplit(".", 1)[-1])
    if clear_cookie_helper_def is None or _call_line(clear_cookie_helper_def, "clear_auth_cookie") is None:
        violations.append(
            f"{_rel(adapter_path)}:{contract['self_target_cookie_clear_helper']}:backend.control_plane.adapters.auth_cookies.clear_auth_cookie"
        )
    if "CHANNEL_USER_EVENTS" not in source:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_channel']}")
    assert violations == []


def test_credential_mutation_gate_requires_invalidation_audit_commit_cookie_clear_and_publish_order() -> None:
    contract = export_auth_boundary_contract()["credential_mutation_contract"]
    scenarios = (
        ("pin_mutation", "auth_pin.py"),
        ("credential_revocation", "auth_user.py"),
    )
    violations: list[str] = []
    for contract_key, filename in scenarios:
        mutation_contract = contract[contract_key]
        adapter_path = BACKEND_ROOT / "control_plane" / "adapters" / filename
        tree = ast.parse(adapter_path.read_text(encoding="utf-8-sig"), filename=str(adapter_path))
        source = adapter_path.read_text(encoding="utf-8-sig")
        function_name = mutation_contract["entrypoint"].rsplit(".", 1)[-1]
        function_def = _function_def(tree, function_name)
        if function_def is None:
            violations.append(f"{_rel(adapter_path)}:missing:{function_name}")
            continue
        invalidate_line = _call_line(function_def, "revoke_all_user_sessions")
        audit_line = _call_line(function_def, mutation_contract["audit_helper"].rsplit(".", 1)[-1])
        commit_line = _call_line(function_def, "commit")
        clear_cookie_line = _call_line(function_def, mutation_contract["cookie_clear_helper"].rsplit(".", 1)[-1])
        publish_line = _call_line(function_def, mutation_contract["event_helper"].rsplit(".", 1)[-1])
        if invalidate_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['session_invalidation_entrypoint']}")
            continue
        if audit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{mutation_contract['audit_helper']}")
            continue
        if commit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:db.commit")
            continue
        if clear_cookie_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{mutation_contract['cookie_clear_helper']}")
            continue
        if publish_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{mutation_contract['event_helper']}")
            continue
        if not (invalidate_line < audit_line < commit_line < clear_cookie_line < publish_line):
            violations.append(f"{_rel(adapter_path)}:{function_name}:order")
        audit_helper_def = _function_def(tree, mutation_contract["audit_helper"].rsplit(".", 1)[-1])
        if audit_helper_def is None or _call_line(audit_helper_def, "log_audit") is None:
            violations.append(f"{_rel(adapter_path)}:{mutation_contract['audit_helper']}:{contract['audit_entrypoint']}")
        event_helper_def = _function_def(tree, mutation_contract["event_helper"].rsplit(".", 1)[-1])
        if event_helper_def is None or _call_line(event_helper_def, "publish_control_event") is None:
            violations.append(f"{_rel(adapter_path)}:{mutation_contract['event_helper']}:{contract['event_entrypoint']}")
        clear_cookie_helper_def = _function_def(tree, mutation_contract["cookie_clear_helper"].rsplit(".", 1)[-1])
        if clear_cookie_helper_def is None or _call_line(clear_cookie_helper_def, "clear_auth_cookie") is None:
            violations.append(f"{_rel(adapter_path)}:{mutation_contract['cookie_clear_helper']}:{contract['cookie_clear_entrypoint']}")
        if "CHANNEL_USER_EVENTS" not in source:
            violations.append(f"{_rel(adapter_path)}:{mutation_contract['event_helper']}:{contract['event_channel']}")
    assert violations == []


def test_session_mutation_gate_requires_invalidation_audit_commit_cookie_clear_and_publish_order() -> None:
    contract = export_auth_boundary_contract()["session_mutation_contract"]
    adapter_path = BACKEND_ROOT / "control_plane" / "adapters" / "sessions.py"
    tree = ast.parse(adapter_path.read_text(encoding="utf-8-sig"), filename=str(adapter_path))
    source = adapter_path.read_text(encoding="utf-8-sig")
    function_names = (
        "revoke_my_session",
        "revoke_all_my_sessions",
        "revoke_all_user_sessions_admin",
    )
    violations: list[str] = []
    for function_name in function_names:
        function_def = _function_def(tree, function_name)
        if function_def is None:
            violations.append(f"{_rel(adapter_path)}:missing:{function_name}")
            continue
        invalidation_name = "revoke_owned_session" if function_name == "revoke_my_session" else "revoke_all_user_sessions"
        invalidation_entrypoint = (
            contract["single_session_invalidation_entrypoint"]
            if function_name == "revoke_my_session"
            else contract["bulk_session_invalidation_entrypoint"]
        )
        invalidate_line = _call_line(function_def, invalidation_name)
        audit_line = _call_line(function_def, contract["audit_helper"].rsplit(".", 1)[-1])
        commit_line = _call_line(function_def, "commit")
        clear_cookie_line = _call_line(function_def, contract["cookie_clear_helper"].rsplit(".", 1)[-1])
        publish_line = _call_line(function_def, contract["event_helper"].rsplit(".", 1)[-1])
        if invalidate_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{invalidation_entrypoint}")
            continue
        if audit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['audit_helper']}")
            continue
        if commit_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:db.commit")
            continue
        if clear_cookie_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['cookie_clear_helper']}")
            continue
        if publish_line is None:
            violations.append(f"{_rel(adapter_path)}:{function_name}:{contract['event_helper']}")
            continue
        if not (invalidate_line < audit_line < commit_line < clear_cookie_line < publish_line):
            violations.append(f"{_rel(adapter_path)}:{function_name}:order")
    audit_helper_def = _function_def(tree, contract["audit_helper"].rsplit(".", 1)[-1])
    if audit_helper_def is None or _call_line(audit_helper_def, "log_audit") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['audit_helper']}:{contract['audit_entrypoint']}")
    event_helper_def = _function_def(tree, contract["event_helper"].rsplit(".", 1)[-1])
    if event_helper_def is None or _call_line(event_helper_def, "publish_control_event") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_entrypoint']}")
    clear_cookie_helper_def = _function_def(tree, contract["cookie_clear_helper"].rsplit(".", 1)[-1])
    if clear_cookie_helper_def is None or _call_line(clear_cookie_helper_def, "clear_auth_cookie") is None:
        violations.append(f"{_rel(adapter_path)}:{contract['cookie_clear_helper']}:backend.control_plane.adapters.auth_cookies.clear_auth_cookie")
    if "CHANNEL_SESSION_EVENTS" not in source:
        violations.append(f"{_rel(adapter_path)}:{contract['event_helper']}:{contract['event_channel']}")
    assert violations == []


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
