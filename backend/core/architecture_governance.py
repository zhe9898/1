from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.core.aggregate_owner_registry import export_aggregate_owner_registry
from backend.core.compatibility_adapter import export_status_compatibility_rules
from backend.core.control_plane import export_surface_registry
from backend.core.execution_fault_isolation import export_fault_isolation_contract
from backend.core.extension_guard import export_extension_budget_contract
from backend.core.lease_service import export_lease_service_contract
from backend.core.runtime_policy_resolver import export_runtime_policy_contract


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
        summary="Control-plane surfaces are defined in backend code and validated against the kernel capability registry before export.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.control_plane", "backend.core.kernel_capabilities"),
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
        summary="Runtime policy reads flow through RuntimePolicyResolver and PolicyStore, with a static gate blocking direct runtime system.yaml parsing outside the allowlist.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.runtime_policy_resolver", "backend.core.scheduling_policy_store"),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_runtime_policy_gate_blocks_runtime_system_yaml_reads_outside_allowlist",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A3",
        title="Core state write-path constraint",
        priority="P0",
        maturity="enforced",
        summary="Static analysis restricts writes to protected aggregate fields so API, worker, and sentinel code paths cannot mutate them outside declared owner services.",
        enforcement_layers=("tests",),
        source_modules=("backend.core.aggregate_owner_registry",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_state_path_gate_only_allows_owner_services_for_core_field_writes",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A4",
        title="LeaseService single-writer constraint",
        priority="P0",
        maturity="enforced",
        summary="Lease lifecycle writes are centralized in LeaseService and backed by a dedicated static gate for lease-owned fields.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.lease_service",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_lease_gate_only_allows_lease_service_writes",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A5",
        title="Compatibility layer boundary constraint",
        priority="P0",
        maturity="enforced",
        summary="Transport compatibility for legacy state aliases has been retired; the compatibility adapter export now attests that only canonical values are accepted.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.compatibility_adapter",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_status_compatibility_rules_export_release_window_metadata",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A6",
        title="Fault isolation constraint",
        priority="P1",
        maturity="enforced",
        summary="Execution-plane fault isolation is exported as a dedicated contract covering stale-lease guards, lease-renewal abandonment, timeout-bounded final reporting, and graceful drain behavior.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.execution_fault_isolation", "backend.api.jobs.lifecycle", "backend.workers.control_plane_worker"),
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
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.extension_guard",),
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
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.control_plane",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_surface_registry_exports_capability_scope_pack_and_policy_trace",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A9",
        title="Policy snapshot writeback constraint",
        priority="P1",
        maturity="enforced",
        summary="Dispatch audit context persists policy, quota, and governance version snapshots alongside scheduling decision linkage.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.api.jobs.dispatch", "backend.core.lease_service"),
        gate_tests=("backend.tests.unit.test_control_plane_protocol_contracts::test_pull_jobs_assigns_attempt_and_lease_token",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A10",
        title="Aggregate ownership constraint",
        priority="P0",
        maturity="enforced",
        summary="Aggregate ownership is declared in a dedicated registry that maps each aggregate root to one owner service and its controlled modules.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.aggregate_owner_registry",),
        gate_tests=("backend.tests.unit.test_architecture_governance_gates::test_aggregate_owner_registry_is_unique_and_complete",),
    ),
    ArchitectureGovernanceRule(
        rule_id="A11",
        title="Extension resource budget constraint",
        priority="P1",
        maturity="enforced",
        summary="Scheduling extensions are bounded by execution time, payload size, audit size, external-call count, and per-phase cardinality budgets.",
        enforcement_layers=("core", "tests"),
        source_modules=("backend.core.extension_guard",),
        gate_tests=(
            "backend.tests.unit.test_architecture_governance_gates::test_extension_budget_guard_rejects_post_bind_external_call_over_budget",
            "backend.tests.unit.test_architecture_governance_gates::test_extension_payload_budget_guard_enforces_64kib_limit",
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
            "surface_registry": "backend.core.control_plane.export_surface_registry",
            "runtime_policy_contract": "backend.core.runtime_policy_resolver.export_runtime_policy_contract",
            "lease_service_contract": "backend.core.lease_service.export_lease_service_contract",
            "fault_isolation_contract": "backend.core.execution_fault_isolation.export_fault_isolation_contract",
            "aggregate_owner_registry": "backend.core.aggregate_owner_registry.export_aggregate_owner_registry",
            "compatibility_rules": "backend.core.compatibility_adapter.export_status_compatibility_rules",
            "extension_budget_contract": "backend.core.extension_guard.export_extension_budget_contract",
        },
        "registries": {
            "surface_registry": export_surface_registry(),
            "runtime_policy_contract": export_runtime_policy_contract(),
            "lease_service_contract": export_lease_service_contract(),
            "fault_isolation_contract": export_fault_isolation_contract(),
            "aggregate_owner_registry": export_aggregate_owner_registry(),
            "status_compatibility_rules": export_status_compatibility_rules(),
            "extension_budget_contract": export_extension_budget_contract(),
        },
    }
