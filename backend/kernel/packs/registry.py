from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True, slots=True)
class PackSelectorContract:
    required_capabilities: tuple[str, ...] = ()
    target_zone: str | None = None
    target_executors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PackDefinition:
    key: str
    label: str
    category: str
    description: str
    delivery_stage: str = "contract-only"
    services: tuple[str, ...] = ()
    routers: tuple[str, ...] = ()
    capability_keys: tuple[str, ...] = ()
    selector: PackSelectorContract = field(default_factory=PackSelectorContract)
    deployment_boundary: str = ""
    runtime_owner: str = ""
    includes: tuple[str, ...] = ()
    gateway_image_target: str | None = None
    allow_all_services: bool = False


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
        selector=PackSelectorContract(required_capabilities=("iot.adapter",), target_zone="home"),
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
        selector=PackSelectorContract(required_capabilities=("ops.observe",), target_zone="ops"),
        deployment_boundary="Runs as dedicated observability services and sidecars, not inside gateway request handlers.",
        runtime_owner="ops-stack",
    ),
    "media-pack": PackDefinition(
        key="media-pack",
        label="Media Pack",
        category="media",
        description="Asset ingest, export, and secure shredding remain an opt-in media extension domain outside the default kernel.",
        delivery_stage="runtime-present",
        routers=("assets", "portability"),
        capability_keys=("pack.media", "media.asset", "media.portability"),
        selector=PackSelectorContract(target_zone="media"),
        deployment_boundary="Runs as an opt-in gateway extension domain and is mounted only when explicitly selected via packs.",
        runtime_owner="gateway-extension",
    ),
    "health-pack": PackDefinition(
        key="health-pack",
        label="Health Pack",
        category="health",
        description="Native iOS/Android health ingestion boundary for HealthKit and Health Connect clients.",
        delivery_stage="runtime-present",
        routers=("health",),
        capability_keys=("pack.health", "health.ingest"),
        selector=PackSelectorContract(
            required_capabilities=("health.ingest",),
            target_zone="mobile",
            target_executors=("swift-native", "kotlin-native"),
        ),
        deployment_boundary="Uses native clients and connector ingestion; health libraries do not enter the Python gateway runtime.",
        runtime_owner="native-client",
    ),
    "vector-pack": PackDefinition(
        key="vector-pack",
        label="Vector / AI Pack",
        category="ai",
        description="Embedding, indexing, semantic search, and rerank capabilities dispatched to worker or search services.",
        delivery_stage="runtime-present",
        routers=("search",),
        capability_keys=("pack.vector", "vector.embed", "vector.index", "vector.search", "vector.rerank"),
        selector=PackSelectorContract(
            required_capabilities=("vector.search",),
            target_zone="search",
            target_executors=("vector-worker", "search-service"),
        ),
        deployment_boundary="Runs as worker/search services and keeps semantic workloads out of the default kernel path.",
        runtime_owner="worker-service",
    ),
}


def get_pack_definition(pack_key: str) -> PackDefinition | None:
    return PACK_DEFINITIONS.get(pack_key)


def available_pack_definitions() -> tuple[PackDefinition, ...]:
    return tuple(PACK_DEFINITIONS[key] for key in PACK_DEFINITIONS)
