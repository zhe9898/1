"""
Render-manifest helpers extracted from compiler CLI.
Keeps manifest contract testable and stable as IaC single source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.iac_core.profiles import HOST_FIRST_DEPLOYMENT_MODEL, classify_container_services

DEFAULT_PRODUCT_NAME = "ZEN70 Gateway Kernel"


def resolve_product_name(deployment_cfg: Mapping[str, Any] | None) -> str:
    raw = str((deployment_cfg or {}).get("product") or "").strip()
    return raw or DEFAULT_PRODUCT_NAME


def build_render_manifest(
    *,
    rendered_at: str,
    source: str,
    product: str,
    profile: str,
    requested_packs: list[str],
    resolved_packs: list[str],
    gateway_image_target: str,
    policy_version: int,
    policy_file: str,
    container_service_names: list[str],
    host_service_names: list[str],
    policy_injections: list[dict[str, str]],
    tier3_warning_count: int,
) -> dict[str, Any]:
    rendered_containers = _sorted_rendered_service_names(container_service_names)
    rendered_host_processes = _sorted_rendered_service_names(host_service_names)
    infrastructure_containers, optional_pack_containers = classify_container_services(rendered_containers)
    runtime_services = sorted(set(rendered_containers) | set(rendered_host_processes))
    normalized_policy_injections = _normalized_policy_injections(policy_injections)
    return {
        "rendered_at": rendered_at,
        "source": source,
        "product": product,
        "profile": profile,
        "requested_packs": requested_packs,
        "resolved_packs": resolved_packs,
        "gateway_image_target": gateway_image_target,
        "policy_version": policy_version,
        "policy_file": policy_file,
        "deployment_model": HOST_FIRST_DEPLOYMENT_MODEL,
        "container_services_rendered": rendered_containers,
        "infrastructure_containers_rendered": infrastructure_containers,
        "optional_pack_containers_rendered": optional_pack_containers,
        "host_processes_rendered": rendered_host_processes,
        "runtime_services_rendered": runtime_services,
        "migration_copy_plan": {
            "host_processes": rendered_host_processes,
            "infrastructure_containers": infrastructure_containers,
            "optional_pack_containers": optional_pack_containers,
        },
        "policy_injections": normalized_policy_injections,
        "policy_injection_count": len(normalized_policy_injections),
        "tier3_warnings": [],
        "tier3_warning_count": max(tier3_warning_count, 0),
    }


def project_rendered_service_names(services: list[dict[str, Any]]) -> list[str]:
    return _sorted_rendered_service_names([str(service.get("name")) for service in services if isinstance(service, dict) and service.get("name")])


def _sorted_rendered_service_names(service_names: list[str]) -> list[str]:
    return sorted({name.strip() for name in service_names if isinstance(name, str) and name.strip()})


def _normalized_policy_injections(policy_injections: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for injection in policy_injections:
        if not isinstance(injection, dict):
            continue
        rule = str(injection.get("rule") or "").strip()
        service = str(injection.get("service") or "").strip()
        if not rule and not service:
            continue
        normalized.append({"rule": rule, "service": service})
    return normalized
