"""
Profile and pack normalization for Gateway Kernel releases.
Runtime-visible profile surface is fixed to `gateway-kernel`.
Development builds keep profile selection strict and require explicit
canonical pack keys for optional capability domains.
"""

from __future__ import annotations

from typing import Iterable

from backend.kernel.packs.presets import (
    is_profile_preset_known,
    requested_pack_keys,
)
from backend.kernel.profiles.public_profile import PUBLIC_PROFILE_SURFACE, normalize_gateway_profile
from backend.kernel.topology.pack_selection import (
    resolve_gateway_image_target as resolve_registry_gateway_image_target,
)
from backend.kernel.topology.pack_selection import resolve_pack_keys, selected_service_allowlist

CORE_SERVICES: tuple[str, ...] = (
    "caddy",
    "gateway",
    "redis",
    "postgres",
    "sentinel",
    "docker-proxy",
    "runner-agent",
)


def normalize_profile(raw_profile: object) -> str:
    return normalize_gateway_profile(raw_profile)


def resolve_requested_pack_keys(profile: object, raw_packs: object = None) -> tuple[str, ...]:
    return requested_pack_keys(profile=profile, raw_packs=raw_packs)


def resolve_effective_pack_keys(profile: object, raw_packs: object = None) -> tuple[str, ...]:
    return resolve_pack_keys(profile=profile, raw_packs=raw_packs)


def allowed_services_for_profile(
    profile: object,
    *,
    selected_packs: object = None,
) -> set[str] | None:
    return selected_service_allowlist(
        profile=profile,
        raw_packs=selected_packs,
        core_services=CORE_SERVICES,
    )


def resolve_gateway_image_target(
    profile: object,
    *,
    selected_packs: object = None,
) -> str:
    return resolve_registry_gateway_image_target(profile=profile, raw_packs=selected_packs)


def is_profile_known(profile: object, supported: Iterable[str] | None = None) -> bool:
    canonical = str(profile or "").strip().lower()
    if supported is None:
        return is_profile_preset_known(canonical)
    return canonical in set(supported)
