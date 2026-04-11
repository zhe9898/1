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
    }


__all__ = (
    "AUTH_REQUEST_TENANT_MODELS",
    "DIRECT_AUDIT_HELPER_ALLOWLIST",
    "DIRECT_COOKIE_POLICY_ALLOWLIST",
    "DIRECT_ROLE_CLAIM_ALLOWLIST",
    "DIRECT_TENANT_CLAIM_ALLOWLIST",
    "FORBIDDEN_DIRECT_AUDIT_HELPERS",
    "FORBIDDEN_RAW_COOKIE_PATTERNS",
    "FORBIDDEN_DIRECT_ROLE_PATTERNS",
    "FORBIDDEN_DIRECT_TENANT_PATTERNS",
    "export_auth_boundary_contract",
)
