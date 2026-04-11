from __future__ import annotations

from backend.kernel.packs.presets import requested_pack_keys
from backend.kernel.packs.registry import PACK_DEFINITIONS, PackDefinition
from backend.kernel.profiles.public_profile import BASE_GATEWAY_PROFILE


def resolve_pack_keys(*, profile: object, raw_packs: object = None) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()

    def walk(pack_key: str) -> None:
        definition = PACK_DEFINITIONS.get(pack_key)
        if definition is None or pack_key in seen:
            return
        seen.add(pack_key)
        ordered.append(pack_key)
        for nested in definition.includes:
            walk(nested)

    for pack_key in requested_pack_keys(profile=profile, raw_packs=raw_packs):
        walk(pack_key)
    return tuple(ordered)


def enabled_pack_definitions(*, profile: object, raw_packs: object = None) -> tuple[PackDefinition, ...]:
    return tuple(PACK_DEFINITIONS[key] for key in resolve_pack_keys(profile=profile, raw_packs=raw_packs) if key in PACK_DEFINITIONS)


def selected_router_names(*, profile: object, raw_packs: object = None) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for definition in enabled_pack_definitions(profile=profile, raw_packs=raw_packs):
        for router_name in definition.routers:
            if router_name in seen:
                continue
            ordered.append(router_name)
            seen.add(router_name)
    return tuple(ordered)


def selected_capability_keys(*, profile: object, raw_packs: object = None) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for definition in enabled_pack_definitions(profile=profile, raw_packs=raw_packs):
        for capability_key in definition.capability_keys:
            if capability_key in seen:
                continue
            ordered.append(capability_key)
            seen.add(capability_key)
    return tuple(ordered)


def selected_service_allowlist(*, profile: object, raw_packs: object = None, core_services: tuple[str, ...]) -> set[str] | None:
    definitions = enabled_pack_definitions(profile=profile, raw_packs=raw_packs)
    if any(definition.allow_all_services for definition in definitions):
        return None
    allowed = set(core_services)
    for definition in definitions:
        allowed.update(definition.services)
    return allowed


def resolve_gateway_image_target(*, profile: object, raw_packs: object = None) -> str:
    del profile, raw_packs
    return BASE_GATEWAY_PROFILE
