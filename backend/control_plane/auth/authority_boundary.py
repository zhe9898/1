from __future__ import annotations

from typing import Final

DIRECT_ROLE_CLAIM_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/kernel/contracts/role_claims.py",
    }
)
DIRECT_TENANT_CLAIM_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/kernel/contracts/tenant_claims.py",
    }
)
DIRECT_AUDIT_HELPER_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/platform/logging/audit.py",
    }
)
DIRECT_COOKIE_POLICY_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/control_plane/auth/cookie_policy.py",
    }
)
AUTH_REQUEST_TENANT_MODELS: Final[tuple[str, ...]] = (
    "backend.control_plane.adapters.models.auth.PasswordLoginRequest",
    "backend.control_plane.adapters.models.auth.PinLoginRequest",
    "backend.control_plane.adapters.models.auth.WebAuthnRegisterBeginRequest",
    "backend.control_plane.adapters.models.auth.WebAuthnLoginBeginRequest",
    "backend.control_plane.adapters.models.auth.WebAuthnLoginCompleteRequest",
)

FORBIDDEN_DIRECT_ROLE_PATTERNS: Final[tuple[str, ...]] = (
    'current_user.get("role")',
    "current_user.get('role')",
    'current_user["role"]',
    "current_user['role']",
)
FORBIDDEN_DIRECT_TENANT_PATTERNS: Final[tuple[str, ...]] = (
    'current_user.get("tenant_id")',
    "current_user.get('tenant_id')",
    'current_user["tenant_id"]',
    "current_user['tenant_id']",
    '(current_user or {}).get("tenant_id")',
    "(current_user or {}).get('tenant_id')",
    '(current_user or {})["tenant_id"]',
    "(current_user or {})['tenant_id']",
)
FORBIDDEN_DIRECT_AUDIT_HELPERS: Final[tuple[str, ...]] = (
    "extract_client_info",
    "sanitize_audit_details",
    "write_audit_log",
)
FORBIDDEN_RAW_COOKIE_PATTERNS: Final[tuple[str, ...]] = (
    "request.cookies",
    "response.set_cookie(",
    "response.delete_cookie(",
)
FORBIDDEN_DYNAMIC_AUTH_ADAPTER_LOOKUPS: Final[tuple[str, ...]] = (
    'sys.modules.get("backend.control_plane.adapters.auth")',
    '__import__("backend.control_plane.adapters.auth"',
    "def _auth_mod",
)
EXPLICIT_AUTH_ADAPTER_SEAMS: Final[tuple[str, ...]] = (
    "backend.control_plane.adapters.auth_shared.set_tenant_context",
    "backend.control_plane.adapters.auth_token_issue.token_response",
    "backend.control_plane.adapters.auth_webauthn.check_webauthn_rate_limit",
    "backend.control_plane.adapters.auth_webauthn.generate_authentication_challenge",
    "backend.control_plane.adapters.auth_webauthn.credential_id_to_base64url",
    "backend.control_plane.adapters.auth_webauthn.expected_challenge_bytes",
    "backend.control_plane.adapters.auth_webauthn.origin_from_request",
    "backend.control_plane.adapters.auth_webauthn.verify_authentication",
)


def export_auth_boundary_contract() -> dict[str, object]:
    return {
        "role_claim_contract": {
            "entrypoint": "backend.kernel.contracts.role_claims.current_user_role",
            "allowlist": sorted(DIRECT_ROLE_CLAIM_ALLOWLIST),
            "forbidden_direct_patterns": list(FORBIDDEN_DIRECT_ROLE_PATTERNS),
        },
        "tenant_claim_contract": {
            "entrypoints": [
                "backend.kernel.contracts.tenant_claims.current_user_tenant_id",
                "backend.kernel.contracts.tenant_claims.require_current_user_tenant_id",
            ],
            "allowlist": sorted(DIRECT_TENANT_CLAIM_ALLOWLIST),
            "forbidden_direct_patterns": list(FORBIDDEN_DIRECT_TENANT_PATTERNS),
        },
        "admin_policy_contract": {
            "module": "backend.control_plane.auth.access_policy",
            "methods": [
                "has_admin_role",
                "is_superadmin_role",
                "require_admin_role",
                "require_superadmin_role",
            ],
        },
        "permission_scope_contract": {
            "module": "backend.control_plane.auth.permissions",
            "methods": [
                "assert_valid_scope",
                "filter_valid_scopes",
                "get_user_scopes",
                "grant_permission",
                "hydrate_scopes_for_role",
                "list_user_permissions",
                "revoke_permission",
            ],
        },
        "tenant_context_contract": {
            "jwt_tenant_db_entrypoint": "backend.control_plane.adapters.deps.get_tenant_db",
            "machine_tenant_db_entrypoint": "backend.control_plane.adapters.deps.get_machine_tenant_db",
        },
        "audit_log_contract": {
            "entrypoint": "backend.platform.logging.audit.log_audit",
            "helper_allowlist": sorted(DIRECT_AUDIT_HELPER_ALLOWLIST),
            "forbidden_direct_helpers": list(FORBIDDEN_DIRECT_AUDIT_HELPERS),
        },
        "cookie_policy_contract": {
            "entrypoints": [
                "backend.control_plane.adapters.auth_cookies.get_auth_cookie_token",
                "backend.control_plane.adapters.auth_cookies.set_auth_cookie",
                "backend.control_plane.adapters.auth_cookies.clear_auth_cookie",
                "backend.control_plane.auth.webauthn_flow_session.ensure_webauthn_flow_session",
                "backend.control_plane.auth.webauthn_flow_session.require_webauthn_flow_session",
                "backend.control_plane.auth.webauthn_flow_session.clear_webauthn_flow_session",
            ],
            "raw_cookie_allowlist": sorted(DIRECT_COOKIE_POLICY_ALLOWLIST),
            "forbidden_direct_patterns": list(FORBIDDEN_RAW_COOKIE_PATTERNS),
        },
        "auth_request_tenant_contract": {
            "entrypoint": "backend.control_plane.adapters.auth_shared.request_tenant_id",
            "request_models": list(AUTH_REQUEST_TENANT_MODELS),
            "tenant_scoped_admin_entrypoints": [
                "backend.control_plane.adapters.auth_shared.bind_admin_scope",
                "backend.control_plane.adapters.auth_shared.enforce_admin_scope",
            ],
            "token_validation_entrypoints": [
                "backend.control_plane.auth.subject_authority.assert_token_subject_active",
                "backend.control_plane.auth.sessions.validate_session_claims",
            ],
            "default_tenant_fallback_allowed": False,
        },
        "auth_actor_contract": {
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
        },
        "session_authority_contract": {
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
        },
        "session_mutation_contract": {
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
        },
        "user_lifecycle_contract": {
            "adapter_module": "backend.control_plane.adapters.user_management",
            "service_module": "backend.control_plane.admin.user_lifecycle",
            "actions": ["suspended", "activated", "deleted"],
            "audit_helper": "backend.control_plane.adapters.user_management._record_user_lifecycle_audit",
            "audit_entrypoint": "backend.platform.logging.audit.log_audit",
            "event_helper": "backend.control_plane.adapters.user_management._publish_user_lifecycle_event",
            "event_entrypoint": "backend.control_plane.adapters.control_events.publish_control_event",
            "event_channel": "user:events",
            "commit_before_publish": True,
        },
        "user_provisioning_contract": {
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
        },
        "permission_mutation_contract": {
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
        },
        "credential_mutation_contract": {
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
        },
        "adapter_dependency_contract": {
            "dynamic_auth_module_lookup_allowed": False,
            "forbidden_patterns": list(FORBIDDEN_DYNAMIC_AUTH_ADAPTER_LOOKUPS),
            "explicit_patch_surfaces": list(EXPLICIT_AUTH_ADAPTER_SEAMS),
        },
    }


__all__ = (
    "AUTH_REQUEST_TENANT_MODELS",
    "DIRECT_AUDIT_HELPER_ALLOWLIST",
    "DIRECT_COOKIE_POLICY_ALLOWLIST",
    "DIRECT_ROLE_CLAIM_ALLOWLIST",
    "DIRECT_TENANT_CLAIM_ALLOWLIST",
    "EXPLICIT_AUTH_ADAPTER_SEAMS",
    "FORBIDDEN_DYNAMIC_AUTH_ADAPTER_LOOKUPS",
    "FORBIDDEN_DIRECT_AUDIT_HELPERS",
    "FORBIDDEN_RAW_COOKIE_PATTERNS",
    "FORBIDDEN_DIRECT_ROLE_PATTERNS",
    "FORBIDDEN_DIRECT_TENANT_PATTERNS",
    "export_auth_boundary_contract",
)
