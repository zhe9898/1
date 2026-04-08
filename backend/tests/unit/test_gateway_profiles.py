from __future__ import annotations

from backend.api.main import get_enabled_router_names, get_gateway_profile
from backend.kernel.profiles.public_profile import normalize_gateway_profile


def test_non_kernel_profile_input_still_collapses_to_kernel_runtime(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PROFILE", "gateway-typo")
    monkeypatch.delenv("GATEWAY_PACKS", raising=False)

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
        "triggers",
        "reservations",
        "evaluations",
    )


def test_gateway_kernel_is_primary_runtime_profile(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PROFILE", "gateway-kernel")

    assert get_gateway_profile() == "gateway-kernel"


def test_non_kernel_inputs_do_not_create_alternate_runtime_surfaces() -> None:
    assert normalize_gateway_profile("gateway-iot") == "gateway-kernel"
    assert normalize_gateway_profile("gateway-ops") == "gateway-kernel"


def test_explicit_iot_pack_enables_iot_surface(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PACKS", "iot-pack")
    enabled = get_enabled_router_names("gateway-kernel")

    assert "iot" in enabled
    assert "scenes" in enabled
    assert "scheduler" in enabled
    assert "media" not in enabled
    assert "agent" not in enabled


def test_explicit_ops_pack_enables_observability_surface(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PACKS", "ops-pack")
    enabled = get_enabled_router_names("gateway-kernel")

    assert "observability" in enabled
    assert "energy" in enabled
    assert "iot" not in enabled
    assert "media" not in enabled


def test_unknown_profile_falls_back_to_gateway_kernel(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PROFILE", "gateway-typo")
    assert get_gateway_profile() == "gateway-kernel"
