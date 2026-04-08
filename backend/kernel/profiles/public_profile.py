from __future__ import annotations

from typing import Final

BASE_GATEWAY_PROFILE: Final[str] = "gateway-kernel"
DEFAULT_PRODUCT_NAME: Final[str] = "ZEN70 Gateway Kernel"
PUBLIC_PROFILE_SURFACE: Final[tuple[str, ...]] = (BASE_GATEWAY_PROFILE,)

_PUBLIC_PROFILE_BY_RUNTIME: Final[dict[str, str]] = {
    profile: profile for profile in PUBLIC_PROFILE_SURFACE
}


def canonical_profile_alias(raw_profile: object) -> str:
    raw = str(raw_profile or "").strip().lower()
    return raw or BASE_GATEWAY_PROFILE


def normalize_gateway_profile(raw_profile: object) -> str:
    del raw_profile
    return BASE_GATEWAY_PROFILE


def public_profile_surface() -> tuple[str, ...]:
    return PUBLIC_PROFILE_SURFACE


def is_public_profile(raw_profile: object) -> bool:
    return str(raw_profile or "").strip().lower() in PUBLIC_PROFILE_SURFACE


def to_public_profile(profile: object) -> str:
    normalized_profile = normalize_gateway_profile(profile)
    return _PUBLIC_PROFILE_BY_RUNTIME.get(normalized_profile, BASE_GATEWAY_PROFILE)
