from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from backend.kernel.execution.workload_semantics import list_workload_descriptors
from backend.kernel.topology.executor_registry import get_executor_registry

RUNTIME_PERSONA_METADATA_KEY = "runtime_persona"
EXECUTOR_CONTRACT_METADATA_KEY = "executor_contract"
EXECUTOR_CONTRACT_SOURCE_METADATA_KEY = "executor_contract_source"

GO_NATIVE_PERSONA = "go-native"
PYTHON_RUNNER_PERSONA = "python-runner"
SHELL_PERSONA = "shell"
SWIFT_NATIVE_PERSONA = "swift-native"
KOTLIN_NATIVE_PERSONA = "kotlin-native"
VECTOR_WORKER_PERSONA = "vector-worker"
SEARCH_SERVICE_PERSONA = "search-service"
UNKNOWN_PERSONA = "unknown"


@dataclass(frozen=True, slots=True)
class RuntimePersonaContract:
    key: str
    label: str
    description: str
    default_executor_contract: str
    allowed_executor_contracts: tuple[str, ...]
    default_node_types: tuple[str, ...] = ()
    default_platforms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeAuthorityBoundary:
    layer: str
    owner: str
    authority: str
    non_authority_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedNodeRuntimeContract:
    persona: str
    executor_contract: str
    supported_workload_kinds: tuple[str, ...]
    source: str


_PERSONA_CONTRACTS: dict[str, RuntimePersonaContract] = {
    GO_NATIVE_PERSONA: RuntimePersonaContract(
        key=GO_NATIVE_PERSONA,
        label="Go Native",
        description="Go runner persona used by backend and runner-agent control-plane surfaces.",
        default_executor_contract="edge-native",
        allowed_executor_contracts=("edge-native", "process", "docker", "wasm"),
        default_node_types=("runner",),
        default_platforms=("windows", "darwin", "linux"),
    ),
    PYTHON_RUNNER_PERSONA: RuntimePersonaContract(
        key=PYTHON_RUNNER_PERSONA,
        label="Python Runner",
        description="Python-side runner persona for process-oriented execution shells.",
        default_executor_contract="process",
        allowed_executor_contracts=("process", "docker"),
        default_node_types=("runner",),
        default_platforms=("windows", "darwin", "linux"),
    ),
    SHELL_PERSONA: RuntimePersonaContract(
        key=SHELL_PERSONA,
        label="Shell",
        description="Generic shell-facing control-plane persona.",
        default_executor_contract="process",
        allowed_executor_contracts=("process", "edge-native", "docker"),
        default_node_types=("runner", "sidecar"),
        default_platforms=("windows", "darwin", "linux"),
    ),
    SWIFT_NATIVE_PERSONA: RuntimePersonaContract(
        key=SWIFT_NATIVE_PERSONA,
        label="Swift Native",
        description="iOS/native-client persona used for HealthKit and mobile bridges.",
        default_executor_contract="process",
        allowed_executor_contracts=("process",),
        default_node_types=("native-client",),
        default_platforms=("ios",),
    ),
    KOTLIN_NATIVE_PERSONA: RuntimePersonaContract(
        key=KOTLIN_NATIVE_PERSONA,
        label="Kotlin Native",
        description="Android/native-client persona used for Health Connect and mobile bridges.",
        default_executor_contract="process",
        allowed_executor_contracts=("process",),
        default_node_types=("native-client",),
        default_platforms=("android",),
    ),
    VECTOR_WORKER_PERSONA: RuntimePersonaContract(
        key=VECTOR_WORKER_PERSONA,
        label="Vector Worker",
        description="Background vector-processing worker persona.",
        default_executor_contract="docker",
        allowed_executor_contracts=("docker", "process"),
        default_node_types=("runner", "sidecar"),
        default_platforms=("linux",),
    ),
    SEARCH_SERVICE_PERSONA: RuntimePersonaContract(
        key=SEARCH_SERVICE_PERSONA,
        label="Search Service",
        description="Search/index-serving persona for vector and semantic retrieval workloads.",
        default_executor_contract="docker",
        allowed_executor_contracts=("docker", "process"),
        default_node_types=("runner", "sidecar"),
        default_platforms=("linux",),
    ),
    UNKNOWN_PERSONA: RuntimePersonaContract(
        key=UNKNOWN_PERSONA,
        label="Unknown",
        description="Fallback persona used when a node has not declared a control-plane runtime identity yet.",
        default_executor_contract="unknown",
        allowed_executor_contracts=(),
    ),
}

_AUTHORITY_BOUNDARIES: tuple[RuntimeAuthorityBoundary, ...] = (
    RuntimeAuthorityBoundary(
        layer="persona",
        owner="control-plane",
        authority="selector vocabulary, bootstrap UX, and operator-facing routing intent",
        non_authority_roles=("does_not_define_workload_kind_compatibility", "does_not_define_scheduler_truth_by_itself"),
    ),
    RuntimeAuthorityBoundary(
        layer="executor_contract",
        owner="kernel",
        authority="node contract compatibility envelope, workload-kind support, and scheduling hard gates",
        non_authority_roles=("not_a_ui_persona", "not_a_browser-facing label vocabulary"),
    ),
    RuntimeAuthorityBoundary(
        layer="workload_kind",
        owner="kernel",
        authority="job semantics, lifecycle hooks, and default resource/QoS model",
        non_authority_roles=("not_a_node_persona", "not_a_runtime-shell implementation detail"),
    ),
)


def _normalize_token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _metadata_text(metadata: Mapping[str, object] | None, key: str) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    return _normalize_token(metadata.get(key))


def list_runtime_persona_contracts() -> tuple[RuntimePersonaContract, ...]:
    keys = (
        GO_NATIVE_PERSONA,
        PYTHON_RUNNER_PERSONA,
        SHELL_PERSONA,
        SWIFT_NATIVE_PERSONA,
        KOTLIN_NATIVE_PERSONA,
        VECTOR_WORKER_PERSONA,
        SEARCH_SERVICE_PERSONA,
        UNKNOWN_PERSONA,
    )
    return tuple(_PERSONA_CONTRACTS[key] for key in keys)


def control_plane_persona_keys() -> tuple[str, ...]:
    return tuple(contract.key for contract in list_runtime_persona_contracts())


def control_plane_persona_options() -> tuple[tuple[str, str], ...]:
    return tuple((contract.key, contract.label) for contract in list_runtime_persona_contracts())


def get_runtime_persona_contract(persona: str) -> RuntimePersonaContract | None:
    return _PERSONA_CONTRACTS.get(_normalize_token(persona))


def is_control_plane_persona(value: str) -> bool:
    normalized = _normalize_token(value)
    return normalized in _PERSONA_CONTRACTS


def list_executor_contract_keys() -> tuple[str, ...]:
    contracts = get_executor_registry().all_contracts()
    return tuple(sorted(contracts))


def executor_contract_options() -> tuple[tuple[str, str], ...]:
    registry = get_executor_registry()
    options: list[tuple[str, str]] = []
    for key in list_executor_contract_keys():
        description = registry.get_or_default(key).description.strip()
        label = key if not description else f"{key} ({description})"
        options.append((key, label))
    return tuple(options)


def supported_workload_kinds_for_executor_contract(executor_contract: str) -> tuple[str, ...]:
    contract = get_executor_registry().get_or_default(_normalize_token(executor_contract))
    if not contract.supported_kinds:
        return ()
    return tuple(sorted(contract.supported_kinds))


def _infer_persona_from_context(
    *,
    executor_contract: str,
    node_type: str,
    operating_system: str,
    zone: str | None,
    metadata: Mapping[str, object] | None,
    capabilities: Sequence[str] | None,
) -> str:
    os_name = _normalize_token(operating_system)
    node_type_name = _normalize_token(node_type)
    zone_name = _normalize_token(zone)
    runtime_hint = _metadata_text(metadata, "runtime")
    caps = {_normalize_token(item) for item in (capabilities or []) if isinstance(item, str)}

    if node_type_name == "native-client" or os_name == "ios":
        return SWIFT_NATIVE_PERSONA
    if os_name == "android":
        return KOTLIN_NATIVE_PERSONA
    if runtime_hint.startswith("python"):
        return PYTHON_RUNNER_PERSONA
    if runtime_hint.startswith("go"):
        return GO_NATIVE_PERSONA
    if zone_name == "search" or any(cap.startswith("vector.") or cap.startswith("search.") for cap in caps):
        return SEARCH_SERVICE_PERSONA
    if executor_contract == "docker":
        return SHELL_PERSONA
    if executor_contract == "process":
        return SHELL_PERSONA
    if executor_contract == "edge-native":
        return GO_NATIVE_PERSONA
    return UNKNOWN_PERSONA


def _assert_persona_contract_compatibility(persona: str, executor_contract: str) -> None:
    contract = get_runtime_persona_contract(persona)
    if contract is None:
        contract = get_runtime_persona_contract(UNKNOWN_PERSONA)
    if contract is None:
        raise ValueError("runtime persona registry is missing the unknown persona contract")
    allowed = tuple(contract.allowed_executor_contracts)
    if not allowed or executor_contract in allowed:
        return
    raise ValueError(f"persona '{persona}' cannot declare executor contract '{executor_contract}' " f"(allowed: {sorted(allowed)})")


def resolve_node_runtime_contract(
    *,
    declared_persona: str,
    declared_executor_contract: str | None,
    node_type: str,
    operating_system: str,
    zone: str | None,
    metadata: Mapping[str, object] | None,
    capabilities: Sequence[str] | None,
    profile_default_executor_contract: str | None = None,
) -> ResolvedNodeRuntimeContract:
    explicit_persona = _normalize_token(declared_persona)
    explicit_contract = _normalize_token(declared_executor_contract)
    meta_persona = _metadata_text(metadata, RUNTIME_PERSONA_METADATA_KEY)
    meta_contract = _metadata_text(metadata, EXECUTOR_CONTRACT_METADATA_KEY)
    profile_contract = _normalize_token(profile_default_executor_contract)

    source_parts: list[str] = []

    if explicit_persona and explicit_persona in _PERSONA_CONTRACTS:
        persona = explicit_persona
        source_parts.append("persona")
    elif meta_persona and meta_persona in _PERSONA_CONTRACTS:
        persona = meta_persona
        source_parts.append("metadata_persona")
    else:
        persona = ""

    if explicit_contract and explicit_contract in get_executor_registry().all_contracts():
        executor_contract = explicit_contract
        source_parts.append("explicit_executor_contract")
    elif meta_contract and meta_contract in get_executor_registry().all_contracts():
        executor_contract = meta_contract
        source_parts.append("metadata_executor_contract")
    elif explicit_persona and explicit_persona in get_executor_registry().all_contracts():
        executor_contract = explicit_persona
        source_parts.append("legacy_executor_field")
    else:
        executor_contract = ""

    if not persona:
        persona = _infer_persona_from_context(
            executor_contract=executor_contract or profile_contract,
            node_type=node_type,
            operating_system=operating_system,
            zone=zone,
            metadata=metadata,
            capabilities=capabilities,
        )
        if persona:
            source_parts.append("inferred_persona")

    if not executor_contract:
        persona_contract = get_runtime_persona_contract(persona)
        if persona_contract is not None:
            executor_contract = persona_contract.default_executor_contract
            source_parts.append("persona_default_executor_contract")
        elif profile_contract:
            executor_contract = profile_contract
            source_parts.append("device_profile_default_executor_contract")
        else:
            executor_contract = "unknown"
            source_parts.append("unknown_executor_contract")

    if persona not in _PERSONA_CONTRACTS:
        persona = UNKNOWN_PERSONA
    _assert_persona_contract_compatibility(persona, executor_contract)
    return ResolvedNodeRuntimeContract(
        persona=persona,
        executor_contract=executor_contract,
        supported_workload_kinds=supported_workload_kinds_for_executor_contract(executor_contract),
        source="+".join(source_parts) or "unknown",
    )


def resolve_persisted_node_runtime_contract(
    *,
    persona: str,
    metadata: Mapping[str, object] | None,
) -> ResolvedNodeRuntimeContract:
    return resolve_node_runtime_contract(
        declared_persona=persona,
        declared_executor_contract=_metadata_text(metadata, EXECUTOR_CONTRACT_METADATA_KEY),
        node_type=str(metadata.get("node_type", "")) if isinstance(metadata, Mapping) else "",
        operating_system=str(metadata.get("os", "")) if isinstance(metadata, Mapping) else "",
        zone=str(metadata.get("zone", "")) if isinstance(metadata, Mapping) else "",
        metadata=metadata,
        capabilities=(),
        profile_default_executor_contract=None,
    )


def node_executor_contract(node: object) -> str:
    persona = _normalize_token(getattr(node, "executor", ""))
    metadata = getattr(node, "metadata_json", None)
    return resolve_persisted_node_runtime_contract(persona=persona, metadata=metadata).executor_contract


def node_supported_workload_kinds(node: object) -> tuple[str, ...]:
    persona = _normalize_token(getattr(node, "executor", ""))
    metadata = getattr(node, "metadata_json", None)
    return resolve_persisted_node_runtime_contract(persona=persona, metadata=metadata).supported_workload_kinds


def persona_supports_ios(persona: str) -> bool:
    contract = get_runtime_persona_contract(persona)
    return bool(contract and "ios" in contract.default_platforms)


def persona_supports_android(persona: str) -> bool:
    contract = get_runtime_persona_contract(persona)
    return bool(contract and "android" in contract.default_platforms)


def export_runtime_contract_taxonomy() -> dict[str, object]:
    registry = get_executor_registry()
    persona_contracts = list_runtime_persona_contracts()
    workload_kinds = tuple(sorted(descriptor.kind for descriptor in list_workload_descriptors()))
    canonical_executor_contracts = {
        name: {
            "description": contract.description,
            "supported_workload_kinds": list(sorted(contract.supported_kinds)),
            "requires_gpu": contract.requires_gpu,
            "min_memory_mb": contract.min_memory_mb,
            "min_cpu_cores": contract.min_cpu_cores,
            "max_concurrency_hint": contract.max_concurrency_hint,
            "stability_tier": contract.stability_tier,
        }
        for name, contract in sorted(registry.all_contracts().items())
    }
    return {
        "runtime_authority_boundaries": [
            {
                "layer": boundary.layer,
                "owner": boundary.owner,
                "authority": boundary.authority,
                "non_authority_roles": list(boundary.non_authority_roles),
            }
            for boundary in _AUTHORITY_BOUNDARIES
        ],
        "control_plane_personas": [
            {
                "key": contract.key,
                "label": contract.label,
                "description": contract.description,
                "default_executor_contract": contract.default_executor_contract,
                "allowed_executor_contracts": list(contract.allowed_executor_contracts),
                "default_node_types": list(contract.default_node_types),
                "default_platforms": list(contract.default_platforms),
            }
            for contract in persona_contracts
        ],
        "persona_to_default_executor_contract": {contract.key: contract.default_executor_contract for contract in persona_contracts},
        "canonical_executor_contracts": canonical_executor_contracts,
        "workload_kinds": list(workload_kinds),
    }
