from __future__ import annotations

from scripts.iac_core.exceptions import PolicyViolation
from scripts.iac_core.manifest import DEFAULT_PRODUCT_NAME, build_render_manifest, resolve_product_name
from scripts.iac_core.profiles import HOST_FIRST_DEPLOYMENT_MODEL


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
        container_service_names=["redis", "redis"],
        host_service_names=["gateway", "gateway"],
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
    assert manifest["deployment_model"] == HOST_FIRST_DEPLOYMENT_MODEL
    assert manifest["container_services_rendered"] == ["redis"]
    assert manifest["infrastructure_containers_rendered"] == ["redis"]
    assert manifest["optional_pack_containers_rendered"] == []
    assert manifest["host_processes_rendered"] == ["gateway"]
    assert manifest["runtime_services_rendered"] == ["gateway", "redis"]
    assert manifest["migration_copy_plan"] == {
        "host_processes": ["gateway"],
        "infrastructure_containers": ["redis"],
        "optional_pack_containers": [],
    }
    assert manifest["policy_injections"] == [{"rule": "REC-001", "service": "gateway", "action": "inject healthcheck"}]
