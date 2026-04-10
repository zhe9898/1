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
        evidence_modules=("backend/kernel/profiles/public_profile.py", "backend/runtime/topology/profile_selection.py"),
    ),
    ExternalRuntimeInvariant(
        key="backend_driven_control_plane",
        statement="control plane is the backend-driven management and orchestration entrypoint.",
        rationale="Surface visibility and control-plane manifests remain server-owned.",
        evidence_modules=(
            "backend/kernel/surfaces/registry.py",
            "backend/control_plane/console/manifest_service.py",
            "backend/control_plane/app/entrypoint.py",
        ),
    ),
    ExternalRuntimeInvariant(
        key="pack_is_contract_not_product",
        statement="pack remains a capability contract and runtime boundary, not a new product surface.",
        rationale="Pack metadata describes capability scope and placement boundaries, not a parallel runtime.",
        evidence_modules=("backend/kernel/packs/registry.py", "backend/runtime/topology/pack_selection.py"),
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
            "backend/extensions/extension_guard.py",
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
            "backend/control_plane/adapters/routes.py",
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
    ExternalRuntimeInvariant(
        key="runtime_persona_executor_workload_chain",
        statement=(
            "Control-plane persona, runtime executor contract, and workload kind remain "
            "distinct layers: persona drives selector UX, executor contract owns hard "
            "compatibility, and workload kinds stay kernel-owned job semantics."
        ),
        rationale="Placement truth must stay explicit instead of leaking through helper-side translation or runtime-shell guesses.",
        evidence_modules=(
            "backend/runtime/topology/runtime_contracts.py",
            "backend/control_plane/adapters/nodes_helpers.py",
            "backend/runtime/scheduling/job_scheduler.py",
            "backend/runtime/scheduling/placement_grpc_client.py",
        ),
    ),
)


TARGET_BACKEND_DOMAINS: Final[tuple[DomainBlueprint, ...]] = (
    DomainBlueprint(
        key="kernel",
        root="backend/kernel",
        summary="System truth: contracts, registries, packs, profiles, policy, and governance.",
        subdomains=(
            SubdomainBlueprint("capabilities", ("Capability facts and canonical capability keys",)),
            SubdomainBlueprint("surfaces", ("Surface contracts", "Surface registry", "Capability traceability")),
            SubdomainBlueprint("packs", ("Pack definitions", "Pack presets", "Capability boundaries")),
            SubdomainBlueprint("profiles", ("Public profile facts", "Profile normalization")),
            SubdomainBlueprint("policy", ("PolicyStore", "RuntimePolicyResolver", "Policy snapshots")),
            SubdomainBlueprint("governance", ("Architecture rules", "Aggregate ownership", "Status contracts")),
            SubdomainBlueprint("contracts", ("Permissions", "Status", "Error contracts")),
        ),
        anti_goals=(
            "No HTTP routers",
            "No runtime orchestration ownership",
            "No extension mutation logic",
            "No platform utility dumping",
        ),
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
        key="runtime",
        root="backend/runtime",
        summary="Moving system behavior: topology admission, scheduling, execution, leases, and fault isolation.",
        subdomains=(
            SubdomainBlueprint("topology", ("Node enrollment", "Profile selection", "Executor contracts")),
            SubdomainBlueprint("scheduling", ("Quota", "Placement", "Reservations", "Scheduling governance")),
            SubdomainBlueprint("execution", ("Job lifecycle", "Attempt lifecycle", "Lease ownership", "Fault isolation")),
        ),
        anti_goals=("No public surface definitions", "No contract registry ownership", "No platform-owned business truth"),
    ),
    DomainBlueprint(
        key="extensions",
        root="backend/extensions",
        summary="Connectors, triggers, workflows, and extension safety behind backend-owned contracts.",
        subdomains=(
            SubdomainBlueprint("connectors", ("Connector registry helpers", "Connector mutation services", "Secret-aware config flows")),
            SubdomainBlueprint("triggers", ("Trigger command services", "Trigger delivery orchestration", "Trigger kind registry")),
            SubdomainBlueprint("workflows", ("Workflow mutation services", "Workflow engine", "Workflow templates")),
            SubdomainBlueprint("sdk", ("Extension manifests", "Extension budgets", "Job kind contracts")),
        ),
        anti_goals=("No kernel fact ownership", "No control-plane entrypoint ownership", "No platform authority shortcuts"),
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
        notes=("Kernel defines what exists; control plane decides what is visible now.",),
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/pack_registry.py",
        target_modules=(
            "backend/kernel/packs/registry.py",
            "backend/kernel/packs/presets.py",
            "backend/runtime/topology/pack_selection.py",
        ),
        why="Pack definitions are kernel contracts, while pack selection and placement consumption belong to runtime topology.",
        sequencing=(
            "Move PackDefinition and PACK_DEFINITIONS into kernel/packs/registry.py.",
            "Keep explicit canonical pack requests in kernel/packs/presets.py.",
            "Move selected router and image-target resolution into runtime/topology.",
        ),
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/core/gateway_profile.py",
        target_modules=(
            "backend/kernel/profiles/public_profile.py",
            "backend/runtime/topology/profile_selection.py",
        ),
        why="Public profile naming is a kernel fact, while enabled router calculation is runtime topology behavior.",
        sequencing=(
            "Keep public profile naming in kernel/profiles.",
            "Move runtime pack and router resolution into runtime/topology.",
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
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/execution/job_lifecycle_service.py",
        target_modules=("backend/runtime/execution/job_lifecycle_service.py",),
        why="Job lifecycle mutations belong to the runtime domain instead of the kernel fact domain.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/execution/lease_service.py",
        target_modules=("backend/runtime/execution/lease_service.py",),
        why="Lease ownership is runtime coordination and should not stay under kernel.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/execution/fault_isolation.py",
        target_modules=("backend/runtime/execution/fault_isolation.py",),
        why="Fault-isolation rules govern runtime behavior and must live with runtime execution code.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/topology/node_enrollment_service.py",
        target_modules=("backend/runtime/topology/node_enrollment_service.py",),
        why="Node enrollment mutates live runtime topology and therefore belongs to the runtime domain.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/scheduling/job_scheduler.py",
        target_modules=("backend/runtime/scheduling/job_scheduler.py",),
        why="Scheduling is live runtime behavior and should not be colocated with kernel fact sources.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/scheduling/scheduling_policy_service.py",
        target_modules=("backend/runtime/scheduling/scheduling_policy_service.py",),
        why="Scheduling policy application is runtime-owned coordination even when policy facts are kernel-backed.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/extensions/connector_service.py",
        target_modules=("backend/extensions/connector_service.py",),
        why="Connector orchestration belongs to the extensions domain, not the kernel truth domain.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/extensions/trigger_command_service.py",
        target_modules=("backend/extensions/trigger_command_service.py",),
        why="Trigger command handling is extension execution behavior and should live in the extensions domain.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/extensions/workflow_command_service.py",
        target_modules=("backend/extensions/workflow_command_service.py",),
        why="Workflow orchestration is extension behavior and should not remain under kernel.",
    ),
    SplitBlueprint(
        status="completed",
        current_module="backend/kernel/extensions/extension_guard.py",
        target_modules=("backend/extensions/extension_guard.py",),
        why="Extension safety belongs to the extensions domain and acts as a gate between extensions and runtime.",
    ),
)


def export_backend_domain_blueprint() -> dict[str, object]:
    return {
        "external_runtime_invariants": [asdict(item) for item in EXTERNAL_RUNTIME_INVARIANTS],
        "domains": [asdict(item) for item in TARGET_BACKEND_DOMAINS],
        "priority_splits": [asdict(item) for item in PRIORITY_SPLITS],
    }
