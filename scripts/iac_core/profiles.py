"""
Profile and pack normalization for Gateway Kernel releases.
Runtime-visible profile surface is fixed to `gateway-kernel`.
Legacy profile presets are accepted only as compatibility inputs and collapse
into kernel + optional pack selection during normalization.
"""

from __future__ import annotations

from typing import Iterable

from backend.core.pack_registry import (
    PROFILE_ALIASES,
    PUBLIC_PROFILE_SURFACE,
    is_profile_preset_known,
    normalize_base_profile,
    normalize_requested_pack_keys as normalize_pack_keys,
    requested_pack_keys,
    resolve_pack_keys,
    resolve_gateway_image_target as resolve_registry_gateway_image_target,
    selected_service_allowlist,
)

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
    return normalize_base_profile(raw_profile)


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
