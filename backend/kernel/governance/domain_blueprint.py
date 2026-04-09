from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Final


@dataclass(frozen=True)
class ExternalRuntimeInvariant:
    key: str
    statement: str
    rationale: str
    evidence_modules: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubdomainBlueprint:
    key: str
    responsibilities: tuple[str, ...]


@dataclass(frozen=True)
class DomainBlueprint:
    key: str
    root: str
    summary: str
    subdomains: tuple[SubdomainBlueprint, ...]
    anti_goals: tuple[str, ...] = ()


@dataclass(frozen=True)
class SplitBlueprint:
    status: str
    current_module: str
    target_modules: tuple[str, ...]
    why: str
    sequencing: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


EXTERNAL_RUNTIME_INVARIANTS: Final[tuple[ExternalRuntimeInvariant, ...]] = (
    ExternalRuntimeInvariant(
        key="kernel_only_runtime_surface",
        statement="gateway-kernel is the only formal runtime surface exposed by the backend runtime.",
        rationale="Packs enrich capability scope, not the product/runtime surface.",
        evidence_modules=("backend/kernel/profiles/public_profile.py", "backend/kernel/topology/profile_selection.py"),
    ),
    ExternalRuntimeInvariant(
        key="backend_driven_control_plane",
        statement="control plane is the backend-driven management and orchestration entrypoint.",
        rationale="Surface visibility and control-plane manifests remain server-owned.",
        evidence_modules=("backend/kernel/surfaces/registry.py", "backend/control_plane/console/manifest_service.py", "backend/api/main.py"),
    ),
    ExternalRuntimeInvariant(
        key="pack_is_contract_not_product",
        statement="pack remains a capability contract and runtime boundary, not a new product surface.",
        rationale="Pack metadata describes capability scope and placement boundaries, not a parallel runtime.",
        evidence_modules=("backend/kernel/packs/registry.py", "backend/kernel/topology/pack_selection.py"),
    ),
    ExternalRuntimeInvariant(
        key="runtime_policy_single_source",
        statement="runtime strategy flows through PolicyStore plus RuntimePolicyResolver only.",
        rationale="Runtime admission and router gates must not splinter across ad hoc reads.",
        evidence_modules=("backend/kernel/policy/policy_store.py", "backend/kernel/policy/runtime_policy_resolver.py"),
    ),
    ExternalRuntimeInvariant(
        key="extensions_follow_contract_chain",
        statement="extension entry remains capability -> surface -> policy -> service contract -> execution contract.",
        rationale="Extensions stay behind backend-owned contracts instead of direct runtime shortcuts.",
        evidence_modules=(
            "backend/kernel/surfaces/registry.py",
            "backend/control_plane/console/manifest_service.py",
            "backend/kernel/governance/architecture_rules.py",
        ),
    ),
    ExternalRuntimeInvariant(
        key="control_plane_event_transport_split",
        statement=(
            "control-plane formal events publish on registered EventBus subjects, "
            "while Redis internal coordination channels stay off the browser realtime chain."
        ),
        rationale="UI realtime delivery and kernel coordination must not share subjects or transport semantics.",
        evidence_modules=(
            "backend/platform/events/channels.py",
            "backend/platform/events/publisher.py",
            "backend/platform/events/subscriber.py",
            "backend/api/routes.py",
        ),
    ),
    ExternalRuntimeInvariant(
        key="redis_runtime_state_is_ephemeral",
        statement=(
            "Redis runtime-state keys remain explicitly ephemeral and non-authoritative, "
            "even when they participate in safety gates or temporary runtime overrides."
        ),
        rationale="Desired state stays in owned aggregates and switch hashes instead of drifting into Redis latches.",
        evidence_modules=(
            "backend/platform/redis/runtime_state.py",
            "backend/sentinel/topology_sentinel.py",
            "backend/sentinel/routing_operator.py",
        ),
    ),
)


TARGET_BACKEND_DOMAINS: Final[tuple[DomainBlueprint, ...]] = (
    DomainBlueprint(
        key="kernel",
        root="backend/kernel",
        summary="Fact sources, policy, topology, scheduling, execution, extensions, and governance.",
        subdomains=(
            SubdomainBlueprint("registry", ("Capability, surface, pack, and profile facts",)),
            SubdomainBlueprint("policy", ("PolicyStore", "RuntimePolicyResolver", "Policy snapshots")),
            SubdomainBlueprint("topology", ("Topology snapshots", "Pack placement", "Router admission inputs")),
            SubdomainBlueprint("scheduling", ("Quota, scoring, solver, and reservation behavior",)),
            SubdomainBlueprint("execution", ("Job lifecycle", "Attempts", "Lease ownership", "Fault isolation")),
            SubdomainBlueprint("extensions", ("Connector, trigger, workflow, and runner-facing extension contracts",)),
            SubdomainBlueprint("governance", ("Architecture rules", "Aggregate ownership", "Status contracts")),
            SubdomainBlueprint("contracts", ("Permissions", "Status", "Error contracts")),
        ),
        anti_goals=("No HTTP routers", "No frontend visibility filtering", "No platform utility dumping"),
    ),
    DomainBlueprint(
        key="control_plane",
        root="backend/control_plane",
        summary="FastAPI bootstrap, auth/session entrypoints, console manifests, and HTTP adapters.",
        subdomains=(
            SubdomainBlueprint("app", ("Bootstrap", "Lifespan", "Middleware", "Router mounting")),
            SubdomainBlueprint("auth", ("Role policy", "Session-facing auth helpers")),
            SubdomainBlueprint("console", ("Manifest visibility filtering", "Console-facing orchestration projections")),
            SubdomainBlueprint("admin", ("Operator-only workflows",)),
            SubdomainBlueprint("adapters", ("HTTP boundary adapters", "Router-level projection handlers")),
        ),
        anti_goals=("No registry ownership", "No topology ownership", "No persistence-owned business logic"),
    ),
    DomainBlueprint(
        key="platform",
        root="backend/platform",
        summary="Shared DB, Redis, logging, telemetry, and security infrastructure.",
        subdomains=(
            SubdomainBlueprint("db", ("Database sessions", "Migration wiring", "Persistence adapters")),
            SubdomainBlueprint("redis", ("Redis clients", "PubSub adapters")),
            SubdomainBlueprint("http", ("Outbound HTTP clients", "Webhook delivery adapters")),
            SubdomainBlueprint("logging", ("Structured logging", "Redaction helpers")),
            SubdomainBlueprint("telemetry", ("Metrics", "Tracing", "Operational signals")),
            SubdomainBlueprint("security", ("Crypto helpers", "Transport-safe primitives")),
        ),
        anti_goals=("No kernel fact ownership", "No control-plane orchestration ownership"),
    ),
)


PRIORITY_SPLITS: Final[tuple[SplitBlueprint, ...]] = (
    SplitBlueprint(
        status="completed",
        current_module="backend/core/control_plane.py",
        target_modules=(
            "backend/kernel/surfaces/contracts.py",
            "backend/kernel/surfaces/registry.py",
            "backend/control_plane/console/manifest_service.py",
        ),
        why="Surface contracts belong to the kernel registry, while visibility filtering belongs to the control plane.",
        sequencing=(
            "Extract ControlPlaneSurface dataclass into kernel facts.",
            "Keep registry export pure and side-effect free.",
            "Move profile, policy, and admin filtering into the manifest service.",
        ),
        notes=("Kernel defines what exists; control plane decides what is visible right now.",),
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/pack_registry.py",
        target_modules=(
            "backend/kernel/packs/registry.py",
            "backend/kernel/packs/presets.py",
            "backend/kernel/topology/pack_selection.py",
        ),
        why="Pack definitions are kernel contracts, while pack selection and placement consumption belong to kernel topology.",
        sequencing=(
            "Move PackDefinition and PACK_DEFINITIONS into kernel/packs/registry.py.",
            "Keep explicit canonical pack requests in kernel/packs/presets.py.",
            "Move selected router and image-target resolution into kernel/topology.",
        ),
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/gateway_profile.py",
        target_modules=(
            "backend/kernel/profiles/public_profile.py",
            "backend/kernel/topology/profile_selection.py",
        ),
        why="Public profile naming is a kernel fact, while enabled router calculation is kernel topology behavior.",
        sequencing=(
            "Keep public profile naming in kernel/profiles.",
            "Move runtime pack and router resolution into kernel/topology.",
        ),
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/runtime_policy_resolver.py",
        target_modules=("backend/kernel/policy/runtime_policy_resolver.py",),
        why="RuntimePolicyResolver is kernel policy logic and should stop living under backend/core.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/scheduling_policy_store.py",
        target_modules=("backend/kernel/policy/policy_store.py",),
        why="PolicyStore is kernel policy state and should anchor the policy subdomain.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/kernel_capabilities.py",
        target_modules=("backend/kernel/capabilities/registry.py",),
        why="Capability ownership belongs to the kernel registry and should not live under backend/core.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/architecture_governance.py",
        target_modules=("backend/kernel/governance/architecture_rules.py",),
        why="Architecture rules define kernel-level invariants and should govern domains from the kernel package.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/aggregate_owner_registry.py",
        target_modules=("backend/kernel/governance/aggregate_owner_registry.py",),
        why="Aggregate ownership is a governance fact, not runtime behavior.",
    ),
)


def export_backend_domain_blueprint() -> dict[str, object]:
    return {
        "external_runtime_invariants": [asdict(item) for item in EXTERNAL_RUNTIME_INVARIANTS],
        "domains": [asdict(item) for item in TARGET_BACKEND_DOMAINS],
        "priority_splits": [asdict(item) for item in PRIORITY_SPLITS],
    }
