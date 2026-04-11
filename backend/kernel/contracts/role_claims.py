from __future__ import annotations

from collections.abc import Mapping

DEFAULT_ROLE = "user"
SUPERADMIN_ROLE = "superadmin"
ADMIN_ROLES = frozenset({"admin", SUPERADMIN_ROLE})
AI_ROUTE_PREFERENCES = frozenset({"auto", "local", "cloud"})

_ROLE_ALIASES = {
    "family_child": "child",
    "kid": "child",
    "\u5b69\u5b50": "child",
    "family_elder": "elder",
    "\u957f\u8f88": "elder",
}


def normalize_role_name(value: object | None, *, fallback: str = DEFAULT_ROLE) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip().lower()
    if not normalized:
        return fallback
    return _ROLE_ALIASES.get(normalized, normalized)


def current_user_role(
    current_user: Mapping[str, object] | None,
    *,
    fallback: str = DEFAULT_ROLE,
) -> str:
    return normalize_role_name((current_user or {}).get("role"), fallback=fallback)


def has_admin_role_value(value: object | None) -> bool:
    return normalize_role_name(value, fallback="") in ADMIN_ROLES


def has_admin_role(current_user: Mapping[str, object] | None) -> bool:
    return has_admin_role_value((current_user or {}).get("role"))


def is_superadmin_role(current_user: Mapping[str, object] | None) -> bool:
    return current_user_role(current_user, fallback="") == SUPERADMIN_ROLE


def is_child_role_value(value: object | None) -> bool:
    return normalize_role_name(value, fallback="") == "child"


def is_elder_role_value(value: object | None) -> bool:
    return normalize_role_name(value, fallback="") == "elder"


def normalize_ai_route_preference(value: object | None, *, fallback: str = "auto") -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in AI_ROUTE_PREFERENCES:
            return normalized
    return fallback


__all__ = (
    "ADMIN_ROLES",
    "AI_ROUTE_PREFERENCES",
    "DEFAULT_ROLE",
    "SUPERADMIN_ROLE",
    "current_user_role",
    "has_admin_role",
    "has_admin_role_value",
    "is_child_role_value",
    "is_elder_role_value",
    "is_superadmin_role",
    "normalize_ai_route_preference",
    "normalize_role_name",
)
