from __future__ import annotations

from backend.capabilities import build_public_capability_matrix
from backend.core.control_plane import load_control_plane_surfaces


def test_kernel_public_capabilities_match_guest_console_surfaces() -> None:
    matrix = build_public_capability_matrix("gateway-kernel", is_admin=False)
    capability_endpoints = {item.endpoint for item in matrix.values() if item.endpoint}
    expected_endpoints = {surface.endpoint for surface in load_control_plane_surfaces() if "gateway-kernel" in surface.profiles and not surface.requires_admin}
    assert capability_endpoints == expected_endpoints


def test_kernel_public_capabilities_include_settings_for_admin() -> None:
    matrix = build_public_capability_matrix("gateway-kernel", is_admin=True)
    assert "gateway.settings" in matrix
