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
from .development_cleanroom import (
    CLEANROOM_FILE_EXTENSIONS,
    CLEANROOM_IGNORED_PATH_PREFIXES,
    FORBIDDEN_TRANSITIONAL_MARKERS,
    GOVERNED_CLEANROOM_ROOTS,
    export_development_cleanroom_contract,
)
from .domain_blueprint import (
    EXTERNAL_RUNTIME_INVARIANTS,
    PRIORITY_SPLITS,
    TARGET_BACKEND_DOMAINS,
    export_backend_domain_blueprint,
)
from .domain_import_fence import (
    BACKEND_DOMAIN_ORDER,
    EXTENSIONS_CONTROL_PLANE_ALLOWLIST,
    GOVERNED_BACKEND_DOMAINS,
    KERNEL_CONTROL_PLANE_ALLOWLIST,
    KERNEL_EXTENSIONS_ALLOWLIST,
    KERNEL_PLATFORM_ALLOWLIST,
    KERNEL_RUNTIME_ALLOWLIST,
    PLATFORM_KERNEL_CONTRACT_PREFIX,
    RUNTIME_CONTROL_PLANE_ALLOWLIST,
    export_backend_domain_import_fence,
)

__all__ = (
    "AGGREGATE_OWNERS",
    "ARCHITECTURE_GOVERNANCE_RULES",
    "AggregateOwner",
    "ArchitectureGovernanceRule",
    "BACKEND_DOMAIN_ORDER",
    "CLEANROOM_FILE_EXTENSIONS",
    "CLEANROOM_IGNORED_PATH_PREFIXES",
    "EXTENSIONS_CONTROL_PLANE_ALLOWLIST",
    "EXTERNAL_RUNTIME_INVARIANTS",
    "FORBIDDEN_TRANSITIONAL_MARKERS",
    "GOVERNED_BACKEND_DOMAINS",
    "GOVERNED_CLEANROOM_ROOTS",
    "KERNEL_CONTROL_PLANE_ALLOWLIST",
    "KERNEL_EXTENSIONS_ALLOWLIST",
    "KERNEL_PLATFORM_ALLOWLIST",
    "KERNEL_RUNTIME_ALLOWLIST",
    "PLATFORM_KERNEL_CONTRACT_PREFIX",
    "PRIORITY_SPLITS",
    "RUNTIME_CONTROL_PLANE_ALLOWLIST",
    "TARGET_BACKEND_DOMAINS",
    "allowed_owner_modules",
    "export_aggregate_owner_registry",
    "export_architecture_governance_rules",
    "export_architecture_governance_snapshot",
    "export_backend_domain_blueprint",
    "export_backend_domain_import_fence",
    "export_development_cleanroom_contract",
    "unique_owner_service_map",
)
