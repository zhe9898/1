from __future__ import annotations

from collections.abc import Mapping

from backend.kernel.contracts.errors import zen

MISSING_TENANT_CLAIM_CODE = "ZEN-TENANT-4002"
MISSING_TENANT_CLAIM_MESSAGE = "Tenant context is missing from authentication token"
MISSING_TENANT_CLAIM_HINT = "Re-authenticate to obtain a token that includes a valid tenant_id claim"


def normalize_tenant_claim(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def current_user_tenant_id(current_user: Mapping[str, object] | None) -> str | None:
    return normalize_tenant_claim((current_user or {}).get("tenant_id"))


def require_current_user_tenant_id(current_user: Mapping[str, object] | None) -> str:
    tenant_id = current_user_tenant_id(current_user)
    if tenant_id is not None:
        return tenant_id
    raise zen(
        MISSING_TENANT_CLAIM_CODE,
        MISSING_TENANT_CLAIM_MESSAGE,
        status_code=403,
        recovery_hint=MISSING_TENANT_CLAIM_HINT,
    )


__all__ = (
    "MISSING_TENANT_CLAIM_CODE",
    "MISSING_TENANT_CLAIM_HINT",
    "MISSING_TENANT_CLAIM_MESSAGE",
    "current_user_tenant_id",
    "normalize_tenant_claim",
    "require_current_user_tenant_id",
)
