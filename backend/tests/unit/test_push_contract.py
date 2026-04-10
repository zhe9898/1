from __future__ import annotations

from backend.control_plane.app.entrypoint import app


def test_push_routes_are_mounted_under_auth_prefix() -> None:
    app.openapi_schema = None
    paths = app.openapi()["paths"]
    assert "/api/v1/auth/push/vapid-public-key" in paths
    assert "/api/v1/auth/push/subscribe" in paths
