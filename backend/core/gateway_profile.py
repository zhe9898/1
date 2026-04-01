from __future__ import annotations

from typing import Final

from backend.core.pack_registry import (
    PROFILE_ALIASES,
    PUBLIC_PROFILE_SURFACE,
    normalize_base_profile,
    normalize_requested_pack_keys,
)
from backend.core.pack_registry import requested_pack_keys as resolve_requested_pack_keys
from backend.core.pack_registry import resolve_gateway_image_target as resolve_pack_registry_gateway_image_target
from backend.core.pack_registry import resolve_pack_keys as resolve_effective_pack_keys
from backend.core.pack_registry import (
    selected_router_names,
)

DEFAULT_PRODUCT_NAME: Final[str] = "ZEN70 Gateway Kernel"

__all__ = (
    "CORE_ROUTER_NAMES",
    "DEFAULT_PRODUCT_NAME",
    "OPTIONAL_ROUTER_NAMES",
    "PROFILE_ALIASES",
    "PUBLIC_PROFILE_SURFACE",
    "PUBLIC_PROFILE_BY_RUNTIME",
    "get_enabled_router_names",
    "is_cluster_enabled",
    "normalize_gateway_pack_keys",
    "normalize_gateway_profile",
    "normalize_requested_pack_keys",
    "resolve_gateway_image_target",
    "resolve_runtime_pack_keys",
    "to_public_profile",
)

CORE_ROUTER_NAMES: Final[tuple[str, ...]] = (
    "routes",
    "auth",
    "settings",
    "profile",
    "console",
    "nodes",
    "jobs",
    "connectors",
)

OPTIONAL_ROUTER_NAMES: Final[tuple[str, ...]] = ("cluster",)

PUBLIC_PROFILE_BY_RUNTIME: Final[dict[str, str]] = {profile: profile for profile in PUBLIC_PROFILE_SURFACE}


def normalize_gateway_profile(raw_profile: str | None) -> str:
    return normalize_base_profile(raw_profile)


def normalize_gateway_pack_keys(
    raw_packs: str | list[str] | tuple[str, ...] | set[str] | None,
    *,
    profile: str | None = None,
) -> tuple[str, ...]:
    return resolve_requested_pack_keys(profile=profile, raw_packs=raw_packs)


def resolve_runtime_pack_keys(
    *,
    profile: str | None,
    raw_packs: str | list[str] | tuple[str, ...] | set[str] | None = None,
) -> tuple[str, ...]:
    return resolve_effective_pack_keys(profile=profile, raw_packs=raw_packs)


def get_enabled_router_names(
    profile: str,
    *,
    selected_packs: str | list[str] | tuple[str, ...] | set[str] | None = None,
) -> tuple[str, ...]:
    enabled = list(CORE_ROUTER_NAMES)
    seen = set(enabled)
    for router_name in selected_router_names(profile=profile, raw_packs=selected_packs):
        if router_name in seen:
            continue
        enabled.append(router_name)
        seen.add(router_name)
    return tuple(enabled)


def to_public_profile(profile: str) -> str:
    normalized_profile = normalize_gateway_profile(profile)
    return PUBLIC_PROFILE_BY_RUNTIME.get(normalized_profile, "gateway-kernel")


def is_cluster_enabled(
    profile: str,
    *,
    selected_packs: str | list[str] | tuple[str, ...] | set[str] | None = None,
) -> bool:

    del profile, selected_packs
    return False


def resolve_gateway_image_target(
    profile: str,
    *,
    selected_packs: str | list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    return resolve_pack_registry_gateway_image_target(profile=profile, raw_packs=selected_packs)
