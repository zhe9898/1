from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.kernel.contracts.status import export_status_compatibility_rules
from backend.kernel.execution.fault_isolation import export_fault_isolation_contract
from backend.kernel.execution.lease_service import export_lease_service_contract
from backend.kernel.extensions.extension_guard import export_extension_budget_contract
from backend.kernel.governance.aggregate_owner_registry import export_aggregate_owner_registry
from backend.kernel.policy.runtime_policy_resolver import export_runtime_policy_contract
from backend.kernel.surfaces.registry import export_surface_registry
from backend.platform.events.channels import export_event_channel_contract
from backend.platform.redis.runtime_state import export_runtime_state_contract


@dataclass(frozen=True, slots=True)
class ArchitectureGovernanceRule:
    rule_id: str
    title: str
    priority: str
    maturity: str
    summary: str
    enforcement_layers: tuple[str, ...]
    source_modules: tuple[str, ...]
    gate_tests: tuple[str, ...]


ARCHITECTURE_GOVERNANCE_RULES: Final[tuple[ArchitectureGovernanceRule, ...]] = (
    ArchitectureGovernanceRule(
        rule_id="A1",
        title="Kernel/Surface relation constraint",
        priority="P0",
        maturity="enforced",
        summary=("Control-plane surfaces are defined in backend code and " "validated against the kernel capability registry before export."),
        enforcement_layers=("kernel", "control_plane", "tests"),
        source_modules=("backend.kernel.surfaces.registry", "backend.kernel.capabilities.registry"),
        gate_tests=(
            "backend.tests.unit.test_architecture_governance_gates::test_surface_registry_exports_capability_scope_pack_and_policy_trace",
            "backend.tests.unit.test_control_plane_runtime_closure::test_kernel_guest_control_plane_surfaces_have_real_api_routes",
        ),
    ),
    ArchitectureGovernanceRule(
        rule_id="A2",
        title="Runtime policy single-source constraint",
        priority="P0",
        maturity="enforced",
        summary=(
            "Runtime policy reads flow through RuntimePolicyResolver and "
            "PolicyStore, with a static gate blocking direct runtime "
            "system.yaml parsing outside the allowlist."
        ),
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.policy.runtime_policy_resolver", "backend.kernel.policy.policy_store"),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_runtime_policy_gate_blocks_runtime_system_yaml_reads_outside_allowlist",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A3",
        title="Core state write-path constraint",
        priority="P0",
        maturity="enforced",
        summary=(
            "Static analysis restricts writes to protected aggregate fields "
            "so API, worker, and sentinel code paths cannot mutate them "
            "outside declared owner services."
        ),
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.governance.aggregate_owner_registry",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_state_path_gate_only_allows_owner_services_for_core_field_writes",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A4",
        title="LeaseService single-writer constraint",
        priority="P0",
        maturity="enforced",
        summary="Lease lifecycle writes are centralized in LeaseService and backed by a dedicated static gate for lease-owned fields.",
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.execution.lease_service",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_lease_gate_only_allows_lease_service_writes",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A5",
        title="Compatibility layer boundary constraint",
        priority="P0",
        maturity="enforced",
        summary=(
            "Transport compatibility for legacy state aliases has been "
            "retired; the canonical status contract export now attests that "
            "only canonical values are accepted."
        ),
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.contracts.status",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_status_compatibility_rules_export_release_window_metadata",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A6",
        title="Fault isolation constraint",
        priority="P1",
        maturity="enforced",
        summary=(
            "Execution-plane fault isolation is exported as a dedicated "
            "contract covering stale-lease guards, lease-renewal "
            "abandonment, timeout-bounded final reporting, and graceful "
            "drain behavior."
        ),
        enforcement_layers=("kernel", "control_plane", "workers", "tests"),
        source_modules=(
            "backend.kernel.execution.fault_isolation",
            "backend.api.jobs.lifecycle_service",
            "backend.workers.control_plane_worker",
        ),
        gate_tests=(
            "backend.tests.unit.test_architecture_governance_gates::test_fault_isolation_contract_matches_runner_and_api_sources",
            "backend.tests.unit.test_control_plane_protocol_contracts::test_complete_job_rejects_stale_lease",
            "backend.tests.unit.test_control_plane_worker_runtime::test_control_plane_worker_runs_out_of_process_and_stops_on_signal",
        ),
    ),
    ArchitectureGovernanceRule(
        rule_id="A7",
        title="Extension safety constraint",
        priority="P0",
        maturity="enforced",
        summary="Extensions must remain manifest-traceable and pass budget validation before entering scheduling phases.",
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.extensions.extension_guard",),
        gate_tests=(
            "backend.tests.unit.test_architecture_governance_gates::test_extension_manifest_guard_requires_traceable_manifest_path",
            "backend.tests.unit.test_architecture_governance_gates::test_extension_budget_guard_rejects_sync_plugin_over_budget",
        ),
    ),
    ArchitectureGovernanceRule(
        rule_id="A8",
        title="Capability/Surface traceability gate",
        priority="P0",
        maturity="enforced",
        summary="Every exported surface carries capability, scope, pack, and policy metadata through the backend-owned surface registry.",
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.surfaces.registry",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_surface_registry_exports_capability_scope_pack_and_policy_trace",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A9",
        title="Policy snapshot writeback constraint",
        priority="P1",
        maturity="enforced",
        summary="Dispatch audit context persists policy, quota, and governance version snapshots alongside scheduling decision linkage.",
        enforcement_layers=("control_plane", "kernel", "tests"),
        source_modules=("backend.api.jobs.pull_service", "backend.kernel.execution.lease_service"),
        gate_tests=("backend.tests.unit.test_control_plane_protocol_contracts::test_pull_jobs_assigns_attempt_and_lease_token",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A10",
        title="Aggregate ownership constraint",
        priority="P0",
        maturity="enforced",
        summary=(
            "Aggregate ownership is declared in a dedicated registry that " "maps each aggregate root to one owner service and its " "controlled modules."
        ),
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.governance.aggregate_owner_registry",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_aggregate_owner_registry_is_unique_and_complete",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A11",
        title="Extension resource budget constraint",
        priority="P1",
        maturity="enforced",
        summary="Scheduling extensions are bounded by execution time, payload size, audit size, external-call count, and per-phase cardinality budgets.",
        enforcement_layers=("kernel", "tests"),
        source_modules=("backend.kernel.extensions.extension_guard",),
        gate_tests=(
            "backend.tests.unit.test_architecture_governance_gates::test_extension_budget_guard_rejects_post_bind_external_call_over_budget",
            "backend.tests.unit.test_architecture_governance_gates::test_extension_payload_budget_guard_enforces_64kib_limit",
        ),
    ),
    ArchitectureGovernanceRule(
        rule_id="A12",
        title="Event transport and runtime-state contract",
        priority="P0",
        maturity="enforced",
        summary=(
            "Formal control-plane events must flow through the registered "
            "EventBus subjects, Redis-only coordination subjects stay off the "
            "browser realtime chain, and Redis runtime-state keys remain "
            "explicitly ephemeral instead of becoming durable authority."
        ),
        enforcement_layers=("platform", "sentinel", "control_plane", "iac", "tests"),
        source_modules=(
            "backend.platform.events.channels",
            "backend.platform.events.publisher",
            "backend.platform.events.subscriber",
            "backend.platform.redis.runtime_state",
        ),
        gate_tests=(
            "backend.tests.unit.test_architecture_governance_gates::test_event_channel_contract_separates_browser_realtime_from_internal_coordination",
            "backend.tests.unit.test_architecture_governance_gates::test_event_transport_gate_blocks_direct_pubsub_usage_outside_event_interfaces",
            "backend.tests.unit.test_architecture_governance_gates::test_runtime_state_contract_is_ephemeral_and_non_authoritative",
            "backend.tests.unit.test_kernel_iac_explicit_contract::test_kernel_iac_runtime_contract_matches_code_backed_event_and_runtime_state_exports",
        ),
    ),
)


def export_architecture_governance_rules() -> dict[str, dict[str, object]]:
    return {
        rule.rule_id: {
            "title": rule.title,
            "priority": rule.priority,
            "maturity": rule.maturity,
            "summary": rule.summary,
            "enforcement_layers": list(rule.enforcement_layers),
            "source_modules": list(rule.source_modules),
            "gate_tests": list(rule.gate_tests),
        }
        for rule in ARCHITECTURE_GOVERNANCE_RULES
    }


def export_architecture_governance_snapshot() -> dict[str, object]:
    return {
        "rules": export_architecture_governance_rules(),
        "entrypoints": {
            "surface_registry": "backend.kernel.surfaces.registry.export_surface_registry",
            "runtime_policy_contract": "backend.kernel.policy.runtime_policy_resolver.export_runtime_policy_contract",
            "lease_service_contract": "backend.kernel.execution.lease_service.export_lease_service_contract",
            "fault_isolation_contract": "backend.kernel.execution.fault_isolation.export_fault_isolation_contract",
            "aggregate_owner_registry": "backend.kernel.governance.aggregate_owner_registry.export_aggregate_owner_registry",
            "compatibility_rules": "backend.kernel.contracts.status.export_status_compatibility_rules",
            "extension_budget_contract": "backend.kernel.extensions.extension_guard.export_extension_budget_contract",
            "event_channel_contract": "backend.platform.events.channels.export_event_channel_contract",
            "runtime_state_contract": "backend.platform.redis.runtime_state.export_runtime_state_contract",
        },
        "registries": {
            "surface_registry": export_surface_registry(),
            "runtime_policy_contract": export_runtime_policy_contract(),
            "lease_service_contract": export_lease_service_contract(),
            "fault_isolation_contract": export_fault_isolation_contract(),
            "aggregate_owner_registry": export_aggregate_owner_registry(),
            "status_compatibility_rules": export_status_compatibility_rules(),
            "extension_budget_contract": export_extension_budget_contract(),
            "event_channel_contract": export_event_channel_contract(),
            "runtime_state_contract": export_runtime_state_contract(),
        },
    }
