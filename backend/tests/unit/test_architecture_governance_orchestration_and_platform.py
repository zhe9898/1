from __future__ import annotations

from .architecture_governance_test_support import (
    BACKEND_ROOT,
    SCANNED_SOURCE_FOLDERS,
    _call_line,
    _dict_literal_string_keys,
    _expr_chain,
    _function_def,
    _python_sources,
    _rel,
    ast,
    control_plane_persona_keys,
    export_event_channel_contract,
    export_runtime_contract_taxonomy,
    export_runtime_state_contract,
    tenant_realtime_subject,
    tenant_subject_token,
)


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
