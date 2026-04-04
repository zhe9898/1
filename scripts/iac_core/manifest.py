"""
Render-manifest helpers extracted from compiler CLI.
Keeps manifest contract testable and stable as IaC single source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
    services_list: list[dict[str, Any]],
    policy_violations: list[Any],
    tier3_warnings: list[str],
) -> dict[str, Any]:
    rendered_services = sorted({svc.get("name") for svc in services_list if isinstance(svc, dict) and svc.get("name")})
    policy_injections = [
        {
            "rule": getattr(v, "rule_id", ""),
            "service": getattr(v, "service", ""),
            "action": getattr(v, "message", ""),
        }
        for v in policy_violations
        if getattr(v, "severity", None) == "warn"
    ]
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
        "services_rendered": rendered_services,
        "policy_injections": policy_injections,
        "tier3_warnings": tier3_warnings[:50],
    }
