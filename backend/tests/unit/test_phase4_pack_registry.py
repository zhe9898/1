from __future__ import annotations

from backend.core.gateway_profile import get_enabled_router_names
from backend.core.pack_registry import (
    available_pack_definitions,
    selected_capability_keys,
    selected_service_allowlist,
)
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




def test_available_pack_registry_contains_phase4_contracts() -> None:
    definitions = {definition.key: definition for definition in available_pack_definitions()}

    assert set(definitions) == {"iot-pack", "ops-pack", "health-pack", "vector-pack"}
    assert definitions["health-pack"].runtime_owner == "native-client"
    assert definitions["health-pack"].delivery_stage == "mvp-skeleton"
    assert "HealthKit" in definitions["health-pack"].description
    assert definitions["iot-pack"].delivery_stage == "runtime-present"
    assert definitions["vector-pack"].delivery_stage == "contract-only"
    assert "vector.search" in definitions["vector-pack"].capability_keys
