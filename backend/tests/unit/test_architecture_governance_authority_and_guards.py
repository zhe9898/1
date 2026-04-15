from __future__ import annotations

from .architecture_governance_test_support import (
    _LEASE_ONLY_FIELDS,
    _OWNER_MODULES_BY_FIELD,
    BACKEND_ROOT,
    ROOT,
    _assignment_pairs,
    _call_line,
    _expr_chain,
    _function_def,
    _python_sources,
    _rel,
    ast,
    export_aggregate_owner_registry,
    export_architecture_governance_rules,
    export_architecture_governance_snapshot,
    export_auth_boundary_contract,
    export_backend_domain_import_fence,
    export_development_cleanroom_contract,
    export_event_channel_contract,
    export_extension_budget_contract,
    export_fault_isolation_contract,
    export_lease_service_contract,
    export_runtime_policy_contract,
    export_runtime_state_contract,
    export_status_compatibility_rules,
    export_surface_registry,
)


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
            alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == contract["module"] for alias in node.names
        }
        defined_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)}
        called_names = {chain[-1] for node in ast.walk(tree) if isinstance(node, ast.Call) for chain in [_expr_chain(node.func)] if chain}

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
        violations.append(f"{_rel(adapter_path)}:{contract['self_target_cookie_clear_helper']}:backend.control_plane.adapters.auth_cookies.clear_auth_cookie")
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
            contract["single_session_invalidation_entrypoint"] if function_name == "revoke_my_session" else contract["bulk_session_invalidation_entrypoint"]
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
