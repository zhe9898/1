"""Kernel topology subdomain."""

from backend.kernel.topology.executor_registry import ExecutorContract, ExecutorRegistry, get_executor_registry
from backend.kernel.topology.node_auth import authenticate_node_request, generate_node_token, hash_node_token, verify_node_token
from backend.kernel.topology.node_enrollment_service import NodeEnrollmentService
from backend.kernel.topology.pack_selection import (
    enabled_pack_definitions,
    resolve_gateway_image_target,
    resolve_pack_keys,
    selected_capability_keys,
    selected_router_names,
    selected_service_allowlist,
)
from backend.kernel.topology.profile_selection import (
    CORE_ROUTER_NAMES,
    OPTIONAL_ROUTER_NAMES,
    get_enabled_router_names,
    is_cluster_enabled,
    normalize_gateway_pack_keys,
    resolve_runtime_pack_keys,
)

__all__ = (
    "CORE_ROUTER_NAMES",
    "ExecutorContract",
    "ExecutorRegistry",
    "NodeEnrollmentService",
    "OPTIONAL_ROUTER_NAMES",
    "authenticate_node_request",
    "enabled_pack_definitions",
    "generate_node_token",
    "get_enabled_router_names",
    "get_executor_registry",
    "hash_node_token",
    "is_cluster_enabled",
    "normalize_gateway_pack_keys",
    "resolve_gateway_image_target",
    "resolve_pack_keys",
    "resolve_runtime_pack_keys",
    "selected_capability_keys",
    "selected_router_names",
    "selected_service_allowlist",
    "verify_node_token",
)
