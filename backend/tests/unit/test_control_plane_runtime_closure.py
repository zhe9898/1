from __future__ import annotations

from backend.api.main import app
from backend.core.control_plane import iter_control_plane_surfaces


def _mounted_api_paths() -> set[str]:
    return {str(route.path) for route in app.routes if str(getattr(route, "path", "")).startswith("/api/")}


def test_kernel_guest_control_plane_surfaces_have_real_api_routes() -> None:
    mounted_paths = _mounted_api_paths()
    expected_paths = {f"/api{surface.endpoint}" for surface in iter_control_plane_surfaces("gateway-kernel", is_admin=False)}

    assert expected_paths == {
        "/api/v1/capabilities",
        "/api/v1/nodes",
        "/api/v1/jobs",
        "/api/v1/connectors",
    }
    assert expected_paths.issubset(mounted_paths)


def test_kernel_admin_control_plane_surfaces_have_real_api_routes() -> None:
    mounted_paths = _mounted_api_paths()
    expected_paths = {f"/api{surface.endpoint}" for surface in iter_control_plane_surfaces("gateway-kernel", is_admin=True)}

    assert expected_paths == {
        "/api/v1/capabilities",
        "/api/v1/nodes",
        "/api/v1/jobs",
        "/api/v1/connectors",
        "/api/v1/settings/schema",
    }
    assert expected_paths.issubset(mounted_paths)
