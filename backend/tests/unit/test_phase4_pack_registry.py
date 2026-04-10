from __future__ import annotations

from backend.kernel.packs.registry import available_pack_definitions
from backend.runtime.topology.pack_selection import selected_capability_keys, selected_service_allowlist
from backend.runtime.topology.profile_selection import get_enabled_router_names
from scripts.iac_core.profiles import CORE_SERVICES


def test_iot_pack_contract_isolated_from_kernel_default() -> None:
    allowlist = selected_service_allowlist(
        profile="gateway-kernel",
        raw_packs=("iot-pack",),
        core_services=CORE_SERVICES,
    )

    assert allowlist is not None
    assert set(CORE_SERVICES).issubset(allowlist)
    assert "mosquitto" in allowlist
    assert "grafana" not in allowlist
    assert get_enabled_router_names("gateway-kernel", selected_packs=("iot-pack",))[-3:] == ("iot", "scenes", "scheduler")
    assert selected_capability_keys(profile="gateway-kernel", raw_packs=("iot-pack",)) == (
        "pack.iot",
        "iot.adapter",
        "iot.scene",
        "iot.rule",
        "iot.device.state",
    )


def test_media_pack_contract_requires_explicit_pack_selection() -> None:
    from backend.control_plane.app.router_admission import KERNEL_ALLOWED_OPTIONAL_ROUTERS, OPTIONAL_ROUTER_MODULES

    allowlist = selected_service_allowlist(
        profile="gateway-kernel",
        raw_packs=("media-pack",),
        core_services=CORE_SERVICES,
    )

    assert allowlist is not None
    assert allowlist == set(CORE_SERVICES)
    assert get_enabled_router_names("gateway-kernel", selected_packs=("media-pack",))[-2:] == ("assets", "portability")
    assert selected_capability_keys(profile="gateway-kernel", raw_packs=("media-pack",)) == (
        "pack.media",
        "media.asset",
        "media.portability",
    )
    assert OPTIONAL_ROUTER_MODULES["assets"] == "backend.control_plane.adapters.assets"
    assert OPTIONAL_ROUTER_MODULES["portability"] == "backend.control_plane.adapters.portability"
    assert "assets" not in KERNEL_ALLOWED_OPTIONAL_ROUTERS
    assert "portability" not in KERNEL_ALLOWED_OPTIONAL_ROUTERS


def test_available_pack_registry_contains_phase4_contracts() -> None:
    definitions = {definition.key: definition for definition in available_pack_definitions()}

    assert set(definitions) == {"iot-pack", "ops-pack", "media-pack", "health-pack", "vector-pack"}
    assert definitions["media-pack"].runtime_owner == "gateway-extension"
    assert definitions["media-pack"].delivery_stage == "runtime-present"
    assert definitions["media-pack"].routers == ("assets", "portability")
    assert definitions["health-pack"].runtime_owner == "native-client"
    assert definitions["health-pack"].delivery_stage == "runtime-present"
    assert "HealthKit" in definitions["health-pack"].description
    assert definitions["iot-pack"].delivery_stage == "runtime-present"
    assert definitions["vector-pack"].delivery_stage == "runtime-present"
    assert "vector.search" in definitions["vector-pack"].capability_keys
