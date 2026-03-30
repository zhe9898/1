from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ============================================================================
# Pack Registry: Capability Contract Layer
# ============================================================================
#
# CRITICAL SEMANTIC BOUNDARY:
#
# Pack declaration = capability contract
# NOT default kernel runtime admission
#
# Pack 定义表达能力合同，不等于默认内核自动加载其路由。
# 默认内核只加载 CORE_ROUTERS + KERNEL_ALLOWED_OPTIONAL_ROUTERS。
#
# Pack 的 routers/services 字段声明"如果启用该 pack，这些路由/服务可用"，
# 但不意味着"默认 gateway-kernel profile 会自动加载这些路由"。
#
# 路由装载由 backend/api/main.py 的显式准入表控制。
# ============================================================================

BASE_GATEWAY_PROFILE: Final[str] = "gateway-kernel"
PUBLIC_PROFILE_SURFACE: Final[tuple[str, ...]] = (BASE_GATEWAY_PROFILE,)


@dataclass(frozen=True)
class PackDefinition:
    key: str
    label: str
    category: str
    description: str
    delivery_stage: str = "contract-only"
    services: tuple[str, ...] = ()
    routers: tuple[str, ...] = ()
    capability_keys: tuple[str, ...] = ()
    selector_hints: tuple[str, ...] = ()
    deployment_boundary: str = ""
    runtime_owner: str = ""
    includes: tuple[str, ...] = ()
    gateway_image_target: str | None = None
    allow_all_services: bool = False


# Legacy-only compatibility inputs. These names are accepted by migration and
# bootstrap entrypoints, but they must not leak back into the runtime profile
# surface, OpenAPI, or release documentation.
PROFILE_ALIASES: Final[dict[str, str]] = {
    "default": "gateway-kernel",
    "core": "gateway-kernel",
    "gateway": "gateway-kernel",
    "safe-kernel": "gateway-kernel",
    "gateway-core": "gateway-kernel",
    "gateway-kernel": "gateway-kernel",
    "iot": "gateway-iot",
    "gateway-iot": "gateway-iot",
    "iot-pack": "gateway-iot",
    "ops": "gateway-ops",
    "gateway-ops": "gateway-ops",
    "ops-pack": "gateway-ops",
}

PACK_ALIASES: Final[dict[str, str]] = {
    "iot": "iot-pack",
    "iot-pack": "iot-pack",
    "ops": "ops-pack",
    "ops-pack": "ops-pack",
    "health": "health-pack",
    "health-pack": "health-pack",
    "vector": "vector-pack",
    "vector-pack": "vector-pack",
    "ai": "vector-pack",
    "ai-pack": "vector-pack",
}

# Legacy compatibility presets. Public runtime/profile responses stay fixed to
# `gateway-kernel`; these presets only expand historical inputs into pack
# selections during normalization.
PROFILE_PACK_PRESETS: Final[dict[str, tuple[str, ...]]] = {
    "gateway-kernel": (),
    "gateway-iot": ("iot-pack",),
    "gateway-ops": ("ops-pack",),
}

PACK_DEFINITIONS: Final[dict[str, PackDefinition]] = {
    "iot-pack": PackDefinition(
        key="iot-pack",
        label="IoT Pack",
        category="iot",
        description="Home and edge automation stack for MQTT, scene execution, and device state control.",
        delivery_stage="runtime-present",
        services=("mosquitto",),
        routers=("iot", "scenes", "scheduler"),
        capability_keys=("pack.iot", "iot.adapter", "iot.scene", "iot.rule", "iot.device.state"),
        selector_hints=("required_capabilities=iot.adapter", "target_zone=home"),
        deployment_boundary="Runs as an edge-side pack and talks to the kernel through jobs and connectors.",
        runtime_owner="edge-service",
        gateway_image_target="gateway-iot",
    ),
    "ops-pack": PackDefinition(
        key="ops-pack",
        label="Ops Pack",
        category="ops",
        description="Observability and operational diagnostics stack kept outside the kernel ingress process.",
        delivery_stage="runtime-present",
        services=("watchdog", "victoriametrics", "grafana", "categraf", "loki", "promtail", "alertmanager", "vmalert"),
        routers=("observability", "energy"),
        capability_keys=("pack.ops", "ops.observe", "ops.energy"),
        selector_hints=("required_capabilities=ops.observe", "target_zone=ops"),
        deployment_boundary="Runs as dedicated observability services and sidecars, not inside gateway request handlers.",
        runtime_owner="ops-stack",
    ),
    "health-pack": PackDefinition(
        key="health-pack",
        label="Health Pack",
        category="health",
        description="Native iOS/Android health ingestion boundary for HealthKit and Health Connect clients.",
        delivery_stage="mvp-skeleton",
        capability_keys=("pack.health", "health.ingest"),
        selector_hints=(
            "required_capabilities=health.ingest",
            "target_zone=mobile",
            "target_executor=swift-native|kotlin-native",
        ),
        deployment_boundary="Uses native clients and connector ingestion; health libraries do not enter the Python gateway runtime.",
        runtime_owner="native-client",
    ),
    "vector-pack": PackDefinition(
        key="vector-pack",
        label="Vector / AI Pack",
        category="ai",
        description="Embedding, indexing, semantic search, and rerank capabilities dispatched to worker or search services.",
        delivery_stage="contract-only",
        routers=("search",),
        capability_keys=("pack.vector", "vector.embed", "vector.index", "vector.search", "vector.rerank"),
        selector_hints=(
            "required_capabilities=vector.search",
            "target_zone=search",
            "target_executor=vector-worker|search-service",
        ),
        deployment_boundary="Runs as worker/search services and keeps semantic workloads out of the default kernel path.",
        runtime_owner="worker-service",
    ),
}


def canonical_profile_preset(raw_profile: object) -> str:
    raw = str(raw_profile or "").strip().lower()
    return PROFILE_ALIASES.get(raw, raw or "gateway-kernel")


def normalize_profile_preset(raw_profile: object) -> str:
    preset = canonical_profile_preset(raw_profile)
    if preset not in PROFILE_PACK_PRESETS:
        return "gateway-kernel"
    return preset


def normalize_base_profile(raw_profile: object) -> str:
    del raw_profile
    return BASE_GATEWAY_PROFILE


def public_profile_surface() -> tuple[str, ...]:
    return PUBLIC_PROFILE_SURFACE


def is_public_profile(raw_profile: object) -> bool:
    return str(raw_profile or "").strip().lower() in PUBLIC_PROFILE_SURFACE


def is_profile_preset_known(raw_profile: object) -> bool:
    return canonical_profile_preset(raw_profile) in PROFILE_PACK_PRESETS


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
        canonical = PACK_ALIASES.get(raw_key.strip().lower())
        if canonical is None or canonical in seen:
            continue
        ordered.append(canonical)
        seen.add(canonical)
    return tuple(ordered)


def default_requested_pack_keys_for_profile(raw_profile: object) -> tuple[str, ...]:
    return PROFILE_PACK_PRESETS.get(normalize_profile_preset(raw_profile), ())


def requested_pack_keys(*, profile: object, raw_packs: object = None) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for key in default_requested_pack_keys_for_profile(profile) + normalize_requested_pack_keys(raw_packs):
        if key in seen:
            continue
        ordered.append(key)
        seen.add(key)
    return tuple(ordered)


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


def available_pack_definitions() -> tuple[PackDefinition, ...]:
    return tuple(PACK_DEFINITIONS[key] for key in PACK_DEFINITIONS)


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
    """Resolve the Docker image target based on enabled packs."""
    definitions = enabled_pack_definitions(profile=profile, raw_packs=raw_packs)
    for definition in definitions:
        if definition.gateway_image_target == "gateway-iot":
            return "gateway-iot"
    return "gateway-kernel"



