from __future__ import annotations

from .architecture_governance_test_support import (
    BACKEND_ROOT,
    SCANNED_SOURCE_FOLDERS,
    _python_sources,
    _rel,
    ast,
    capability_keys,
    export_aggregate_owner_registry,
    export_status_compatibility_rules,
    export_surface_registry,
    unique_owner_service_map,
)


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

    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

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
        "backend/tests/unit/test_architecture_governance_authority_and_guards.py",
        "backend/tests/unit/test_architecture_governance_orchestration_and_platform.py",
        "backend/tests/unit/test_architecture_governance_registry_and_extensions.py",
        "backend/tests/unit/test_architecture_governance_runtime_and_guards.py",
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
