from __future__ import annotations

from typing import Final

BACKEND_DOMAIN_ORDER: Final[tuple[str, ...]] = (
    "kernel",
    "control_plane",
    "runtime",
    "extensions",
    "platform",
)

GOVERNED_BACKEND_DOMAINS: Final[frozenset[str]] = frozenset(BACKEND_DOMAIN_ORDER)

KERNEL_RUNTIME_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/kernel/governance/architecture_rules.py",
        "backend/kernel/policy/runtime_policy_resolver.py",
    }
)

KERNEL_CONTROL_PLANE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/kernel/governance/architecture_rules.py",
    }
)

KERNEL_EXTENSIONS_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/kernel/governance/architecture_rules.py",
    }
)

KERNEL_PLATFORM_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/kernel/governance/architecture_rules.py",
    }
)

RUNTIME_CONTROL_PLANE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/runtime/topology/node_enrollment_service.py",
    }
)

EXTENSIONS_CONTROL_PLANE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "backend/extensions/trigger_service.py",
        "backend/extensions/workflow_command_service.py",
    }
)

PLATFORM_KERNEL_CONTRACT_PREFIX: Final[str] = "backend.kernel.contracts."


def export_backend_domain_import_fence() -> dict[str, object]:
    return {
        "governed_domains": list(BACKEND_DOMAIN_ORDER),
        "allowlists": {
            "kernel_to_control_plane": sorted(KERNEL_CONTROL_PLANE_ALLOWLIST),
            "kernel_to_runtime": sorted(KERNEL_RUNTIME_ALLOWLIST),
            "kernel_to_extensions": sorted(KERNEL_EXTENSIONS_ALLOWLIST),
            "kernel_to_platform": sorted(KERNEL_PLATFORM_ALLOWLIST),
            "runtime_to_control_plane": sorted(RUNTIME_CONTROL_PLANE_ALLOWLIST),
            "extensions_to_control_plane": sorted(EXTENSIONS_CONTROL_PLANE_ALLOWLIST),
        },
        "platform_kernel_contract_prefix": PLATFORM_KERNEL_CONTRACT_PREFIX,
    }


__all__ = (
    "BACKEND_DOMAIN_ORDER",
    "EXTENSIONS_CONTROL_PLANE_ALLOWLIST",
    "GOVERNED_BACKEND_DOMAINS",
    "KERNEL_CONTROL_PLANE_ALLOWLIST",
    "KERNEL_EXTENSIONS_ALLOWLIST",
    "KERNEL_PLATFORM_ALLOWLIST",
    "KERNEL_RUNTIME_ALLOWLIST",
    "PLATFORM_KERNEL_CONTRACT_PREFIX",
    "RUNTIME_CONTROL_PLANE_ALLOWLIST",
    "export_backend_domain_import_fence",
)
