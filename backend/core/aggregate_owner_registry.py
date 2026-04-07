from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AggregateOwner:
    aggregate_key: str
    owner_service: str
    allowed_modules: tuple[str, ...]
    owned_fields: tuple[str, ...]


AGGREGATE_OWNERS: tuple[AggregateOwner, ...] = (
    AggregateOwner(
        aggregate_key="JobAggregate",
        owner_service="JobLifecycleService",
        allowed_modules=("backend/core/job_lifecycle_service.py",),
        owned_fields=("jobs.status",),
    ),
    AggregateOwner(
        aggregate_key="LeaseAggregate",
        owner_service="LeaseService",
        allowed_modules=("backend/core/lease_service.py",),
        owned_fields=(
            "jobs.status",
            "jobs.attempt",
            "jobs.lease_token",
            "jobs.leased_until",
            "job_attempts.status",
            "job_attempts.lease_token",
            "job_attempts.scheduling_decision_id",
        ),
    ),
    AggregateOwner(
        aggregate_key="NodeAggregate",
        owner_service="NodeEnrollmentService",
        allowed_modules=("backend/core/node_enrollment_service.py",),
        owned_fields=("nodes.enrollment_status", "nodes.drain_status", "nodes.drain_until"),
    ),
    AggregateOwner(
        aggregate_key="ConnectorAggregate",
        owner_service="ConnectorService",
        allowed_modules=("backend/core/connector_service.py",),
        owned_fields=("connectors.status", "connectors.config"),
    ),
    AggregateOwner(
        aggregate_key="TriggerAggregate",
        owner_service="TriggerCommandService",
        allowed_modules=("backend/core/trigger_command_service.py",),
        owned_fields=("triggers.status", "trigger_deliveries.status"),
    ),
    AggregateOwner(
        aggregate_key="WorkflowAggregate",
        owner_service="WorkflowCommandService",
        allowed_modules=("backend/core/workflow_command_service.py",),
        owned_fields=("workflows.status",),
    ),
    AggregateOwner(
        aggregate_key="SchedulingPolicyAggregate",
        owner_service="SchedulingPolicyService",
        allowed_modules=("backend/core/scheduling_policy_service.py",),
        owned_fields=("tenant_scheduling_policies.config_version",),
    ),
    AggregateOwner(
        aggregate_key="FeatureFlagAggregate",
        owner_service="FeatureFlagService",
        allowed_modules=("backend/core/feature_flag_service.py",),
        owned_fields=("feature_flags.enabled", "feature_flags.updated_by"),
    ),
)


def export_aggregate_owner_registry() -> dict[str, dict[str, object]]:
    return {
        item.aggregate_key: {
            "owner_service": item.owner_service,
            "allowed_modules": list(item.allowed_modules),
            "owned_fields": list(item.owned_fields),
        }
        for item in AGGREGATE_OWNERS
    }


def unique_owner_service_map() -> dict[str, str]:
    return {item.aggregate_key: item.owner_service for item in AGGREGATE_OWNERS}


def allowed_owner_modules() -> set[str]:
    modules: set[str] = set()
    for item in AGGREGATE_OWNERS:
        modules.update(item.allowed_modules)
    return modules
