from __future__ import annotations

from scripts.iac_core.exceptions import PolicyViolation
from scripts.iac_core.manifest import DEFAULT_PRODUCT_NAME, build_render_manifest, resolve_product_name


def test_product_name_defaults_to_kernel_when_missing() -> None:
    assert resolve_product_name({}) == DEFAULT_PRODUCT_NAME
    assert resolve_product_name({"product": "ZEN70 Gateway Kernel"}) == "ZEN70 Gateway Kernel"


def test_manifest_contract_includes_product_and_warn_injections_only() -> None:
    manifest = build_render_manifest(
        rendered_at="2026-03-26 00:00:00",
        source="system.yaml",
        product="ZEN70 Gateway Kernel",
        profile="gateway-kernel",
        requested_packs=[],
        resolved_packs=[],
        gateway_image_target="gateway-kernel",
        policy_version=2,
        policy_file="iac/policy/core.yaml",
        services_list=[{"name": "gateway"}, {"name": "redis"}, {"name": "gateway"}],
        policy_violations=[
            PolicyViolation(rule_id="REC-001", severity="warn", service="gateway", message="inject healthcheck"),
            PolicyViolation(rule_id="SEC-001", severity="fail", service="gateway", message="forbidden privilege"),
        ],
        tier3_warnings=["w1", "w2"],
    )

    assert manifest["product"] == "ZEN70 Gateway Kernel"
    assert manifest["profile"] == "gateway-kernel"
    assert manifest["requested_packs"] == []
    assert manifest["resolved_packs"] == []
    assert manifest["gateway_image_target"] == "gateway-kernel"
    assert manifest["services_rendered"] == ["gateway", "redis"]
    assert manifest["policy_injections"] == [{"rule": "REC-001", "service": "gateway", "action": "inject healthcheck"}]
