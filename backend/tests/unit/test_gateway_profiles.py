from __future__ import annotations

from backend.api.main import get_enabled_router_names, get_gateway_profile
from backend.core.gateway_profile import normalize_gateway_profile


def test_gateway_core_profile_alias_only_enables_control_plane(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PROFILE", "gateway-core")

    assert get_gateway_profile() == "gateway-kernel"
    assert get_enabled_router_names("gateway-kernel") == (
        "routes",
        "auth",
        "settings",
        "profile",
        "console",
        "nodes",
        "jobs",
        "connectors",
    )


def test_gateway_kernel_is_primary_runtime_profile(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PROFILE", "gateway-kernel")

    assert get_gateway_profile() == "gateway-kernel"


def test_legacy_pack_presets_collapse_to_kernel_profile() -> None:
    assert normalize_gateway_profile("gateway-iot") == "gateway-kernel"
    assert normalize_gateway_profile("gateway-ops") == "gateway-kernel"


def test_gateway_iot_profile_enables_iot_surface_only() -> None:
    enabled = get_enabled_router_names("gateway-iot")

    assert "iot" in enabled
    assert "scenes" in enabled
    assert "scheduler" in enabled
    assert "media" not in enabled
    assert "agent" not in enabled


def test_iot_pack_alias_maps_to_gateway_iot() -> None:
    enabled = get_enabled_router_names("iot-pack")

    assert "iot" in enabled
    assert "scenes" in enabled
    assert "scheduler" in enabled


def test_gateway_ops_profile_enables_observability_surface_only() -> None:
    enabled = get_enabled_router_names("gateway-ops")

    assert "observability" in enabled
    assert "energy" in enabled
    assert "iot" not in enabled
    assert "media" not in enabled


def test_unknown_profile_falls_back_to_gateway_kernel(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PROFILE", "gateway-typo")
    assert get_gateway_profile() == "gateway-kernel"
