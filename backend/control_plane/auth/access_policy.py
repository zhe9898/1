from __future__ import annotations

from collections.abc import Mapping

from backend.core.errors import zen

ADMIN_ROLES = frozenset({"admin", "superadmin"})
SUPERADMIN_ROLE = "superadmin"


def has_admin_role(current_user: Mapping[str, object] | None) -> bool:
    role = str((current_user or {}).get("role") or "").strip().lower()
    return role in ADMIN_ROLES


def is_superadmin_role(current_user: Mapping[str, object] | None) -> bool:
    role = str((current_user or {}).get("role") or "").strip().lower()
    return role == SUPERADMIN_ROLE


def require_admin_role(current_user: dict[str, object]) -> dict[str, object]:
    if not has_admin_role(current_user):
        raise zen(
            "ZEN-AUTH-403",
            "Admin privileges required",
            status_code=403,
            recovery_hint="Sign in with an admin or superadmin account and retry",
        )
    return current_user


def require_superadmin_role(current_user: dict[str, object]) -> dict[str, object]:
    if not is_superadmin_role(current_user):
        raise zen(
            "ZEN-AUTH-403",
            "Superadmin privileges required",
            status_code=403,
            recovery_hint="Sign in with a superadmin account and retry",
        )
    return current_user
