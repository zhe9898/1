"""Kernel governance exports."""

from .aggregate_owner_registry import (
    AGGREGATE_OWNERS,
    AggregateOwner,
    allowed_owner_modules,
    export_aggregate_owner_registry,
    unique_owner_service_map,
)
from .architecture_rules import (
    ARCHITECTURE_GOVERNANCE_RULES,
    ArchitectureGovernanceRule,
    export_architecture_governance_rules,
    export_architecture_governance_snapshot,
)
from .domain_blueprint import (
    EXTERNAL_RUNTIME_INVARIANTS,
    PRIORITY_SPLITS,
    TARGET_BACKEND_DOMAINS,
    export_backend_domain_blueprint,
)

__all__ = (
    "AGGREGATE_OWNERS",
    "ARCHITECTURE_GOVERNANCE_RULES",
    "AggregateOwner",
    "ArchitectureGovernanceRule",
    "EXTERNAL_RUNTIME_INVARIANTS",
    "PRIORITY_SPLITS",
    "TARGET_BACKEND_DOMAINS",
    "allowed_owner_modules",
    "export_aggregate_owner_registry",
    "export_architecture_governance_rules",
    "export_architecture_governance_snapshot",
    "export_backend_domain_blueprint",
    "unique_owner_service_map",
)
