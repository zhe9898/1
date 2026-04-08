"""Kernel topology subdomain."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "CORE_ROUTER_NAMES": ("backend.kernel.topology.profile_selection", "CORE_ROUTER_NAMES"),
    "ExecutorContract": ("backend.kernel.topology.executor_registry", "ExecutorContract"),
    "ExecutorRegistry": ("backend.kernel.topology.executor_registry", "ExecutorRegistry"),
    "NodeEnrollmentService": ("backend.kernel.topology.node_enrollment_service", "NodeEnrollmentService"),
    "OPTIONAL_ROUTER_NAMES": ("backend.kernel.topology.profile_selection", "OPTIONAL_ROUTER_NAMES"),
    "authenticate_node_request": ("backend.kernel.topology.node_auth", "authenticate_node_request"),
    "enabled_pack_definitions": ("backend.kernel.topology.pack_selection", "enabled_pack_definitions"),
    "generate_node_token": ("backend.kernel.topology.node_auth", "generate_node_token"),
    "get_enabled_router_names": ("backend.kernel.topology.profile_selection", "get_enabled_router_names"),
    "get_executor_registry": ("backend.kernel.topology.executor_registry", "get_executor_registry"),
    "hash_node_token": ("backend.kernel.topology.node_auth", "hash_node_token"),
    "is_cluster_enabled": ("backend.kernel.topology.profile_selection", "is_cluster_enabled"),
    "normalize_gateway_pack_keys": ("backend.kernel.topology.profile_selection", "normalize_gateway_pack_keys"),
    "resolve_gateway_image_target": ("backend.kernel.topology.pack_selection", "resolve_gateway_image_target"),
    "resolve_pack_keys": ("backend.kernel.topology.pack_selection", "resolve_pack_keys"),
    "resolve_runtime_pack_keys": ("backend.kernel.topology.profile_selection", "resolve_runtime_pack_keys"),
    "selected_capability_keys": ("backend.kernel.topology.pack_selection", "selected_capability_keys"),
    "selected_router_names": ("backend.kernel.topology.pack_selection", "selected_router_names"),
    "selected_service_allowlist": ("backend.kernel.topology.pack_selection", "selected_service_allowlist"),
    "verify_node_token": ("backend.kernel.topology.node_auth", "verify_node_token"),
}

__all__ = tuple(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = import_module(module_name)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
