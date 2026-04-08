from __future__ import annotations

from typing import Final

from backend.kernel.packs.presets import requested_pack_keys as resolve_requested_pack_keys
from backend.kernel.topology.pack_selection import (
    resolve_gateway_image_target as resolve_pack_registry_gateway_image_target,
)
from backend.kernel.topology.pack_selection import resolve_pack_keys as resolve_effective_pack_keys
from backend.kernel.topology.pack_selection import selected_router_names

CORE_ROUTER_NAMES: Final[tuple[str, ...]] = (
    "routes",
    "auth",
    "settings",
    "profile",
    "console",
    "nodes",
    "jobs",
    "connectors",
    "triggers",
    "reservations",
    "evaluations",
)

OPTIONAL_ROUTER_NAMES: Final[tuple[str, ...]] = ()

__all__ = (
    "CORE_ROUTER_NAMES",
    "OPTIONAL_ROUTER_NAMES",
    "get_enabled_router_names",
    "is_cluster_enabled",
    "normalize_gateway_pack_keys",
    "resolve_gateway_image_target",
    "resolve_runtime_pack_keys",
)


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
