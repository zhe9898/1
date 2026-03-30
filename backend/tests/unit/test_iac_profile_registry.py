from __future__ import annotations

from scripts.iac_core.profiles import (
    is_profile_known,
    normalize_profile,
    resolve_gateway_image_target,
    resolve_requested_pack_keys,
)



def test_unknown_profile_is_rejected_by_registry() -> None:
    assert is_profile_known("gateway-kernel")
    assert is_profile_known("gateway-iot")
    assert is_profile_known("iot-pack")
    assert not is_profile_known("gateway-typo")


def test_unknown_profile_normalizes_to_kernel_default() -> None:
    assert normalize_profile("gateway-typo") == "gateway-kernel"
    assert normalize_profile("gateway-iot") == "gateway-kernel"


def test_gateway_target_mapping_is_stable() -> None:
    assert resolve_gateway_image_target("gateway-kernel") == "gateway-kernel"
    assert resolve_gateway_image_target("gateway-iot") == "gateway-iot"


def test_pack_resolution_supports_legacy_preset_and_explicit_packs() -> None:
    assert resolve_requested_pack_keys("gateway-iot") == ("iot-pack",)
    assert resolve_requested_pack_keys("gateway-kernel", ["vector-pack", "health-pack"]) == (
        "vector-pack",
        "health-pack",
    )
