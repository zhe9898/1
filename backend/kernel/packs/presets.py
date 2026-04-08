from __future__ import annotations

from backend.kernel.packs.registry import PACK_DEFINITIONS
from backend.kernel.profiles.public_profile import BASE_GATEWAY_PROFILE

CANONICAL_PACK_KEYS = frozenset(PACK_DEFINITIONS)


def _coerce_raw_packs(raw_packs: object) -> tuple[str, ...]:
    if raw_packs is None:
        return ()
    if isinstance(raw_packs, str):
        return tuple(part.strip() for part in raw_packs.split(",") if part.strip())
    if isinstance(raw_packs, (list, tuple, set, frozenset)):
        return tuple(str(part).strip() for part in raw_packs if str(part).strip())
    return (str(raw_packs).strip(),) if str(raw_packs).strip() else ()


def normalize_requested_pack_keys(raw_packs: object) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_key in _coerce_raw_packs(raw_packs):
        canonical = raw_key.strip().lower()
        if canonical not in CANONICAL_PACK_KEYS or canonical in seen:
            continue
        ordered.append(canonical)
        seen.add(canonical)
    return tuple(ordered)


def requested_pack_keys(*, profile: object, raw_packs: object = None) -> tuple[str, ...]:
    del profile
    return normalize_requested_pack_keys(raw_packs)


def is_profile_preset_known(raw_profile: object) -> bool:
    return str(raw_profile or "").strip().lower() in {"", BASE_GATEWAY_PROFILE}
