from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_optional
from backend.api.main import app


def test_profile_endpoint_reports_public_profile(monkeypatch) -> None:
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        monkeypatch.setenv("GATEWAY_PROFILE", "gateway-kernel")
        monkeypatch.delenv("GATEWAY_PACKS", raising=False)
        client = TestClient(app)

        response = client.get("/api/v1/profile")
        assert response.status_code == 200, response.text

        envelope = response.json()
        assert envelope["code"] == "ZEN-OK-0"
        data = envelope["data"]
        assert data["product"] == "ZEN70 Gateway Kernel"
        assert data["profile"] == "gateway-kernel"
        assert data["runtime_profile"] == "gateway-kernel"
        assert "nodes" in data["router_names"]
        assert "jobs" in data["router_names"]
        assert data["console_route_names"] == ["dashboard", "nodes", "jobs", "connectors"]
        assert data["capability_keys"] == [
            "gateway.dashboard",
            "gateway.nodes",
            "gateway.jobs",
            "gateway.connectors",
        ]
        assert data["requested_pack_keys"] == []
        assert data["resolved_pack_keys"] == []
        assert {item["pack_key"] for item in data["packs"]} == {
            "iot-pack",
            "ops-pack",
            "health-pack",
            "vector-pack",
        }
        pack_map = {item["pack_key"]: item for item in data["packs"]}
        assert pack_map["health-pack"]["delivery_stage"] == "mvp-skeleton"
        assert pack_map["vector-pack"]["delivery_stage"] == "contract-only"
        assert data["cluster_enabled"] is False
    finally:
        app.dependency_overrides = previous_overrides


def test_profile_endpoint_reports_selected_pack_contracts(monkeypatch) -> None:
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        monkeypatch.setenv("GATEWAY_PROFILE", "gateway-kernel")
        monkeypatch.setenv("GATEWAY_PACKS", "iot-pack,health-pack")
        client = TestClient(app)

        response = client.get("/api/v1/profile")
        assert response.status_code == 200, response.text

        data = response.json()["data"]
        assert data["profile"] == "gateway-kernel"
        assert data["runtime_profile"] == "gateway-kernel"
        assert data["requested_pack_keys"] == ["iot-pack", "health-pack"]
        assert data["resolved_pack_keys"] == ["iot-pack", "health-pack"]
        pack_map = {item["pack_key"]: item for item in data["packs"]}
        assert pack_map["iot-pack"]["selected"] is True
        assert pack_map["health-pack"]["selected"] is True
        assert pack_map["health-pack"]["delivery_stage"] == "mvp-skeleton"
        assert pack_map["iot-pack"]["router_names"] == ["iot", "scenes", "scheduler"]
        assert pack_map["iot-pack"]["services"] == ["mosquitto"]
        assert pack_map["health-pack"]["services"] == []
        assert "iot" in data["router_names"]
        assert "scheduler" in data["router_names"]
    finally:
        app.dependency_overrides = previous_overrides


def test_console_menu_hides_admin_entries_without_token() -> None:
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        client = TestClient(app)

        response = client.get("/api/v1/console/menu")
        assert response.status_code == 200, response.text

        envelope = response.json()
        assert envelope["code"] == "ZEN-OK-0"
        assert envelope["data"]["product"] == "ZEN70 Gateway Kernel"
        items = envelope["data"]["items"]
        route_names = [item["route_name"] for item in items]
        assert "dashboard" in route_names
        assert "nodes" in route_names
        assert "jobs" in route_names
        assert "connectors" in route_names
        assert "settings" not in route_names

        cap_response = client.get("/api/v1/capabilities")
        assert cap_response.status_code == 200, cap_response.text
        capability_keys = set(cap_response.json()["data"].keys())
        assert capability_keys == {
            "gateway.dashboard",
            "gateway.nodes",
            "gateway.jobs",
            "gateway.connectors",
        }

        overview = client.get("/api/v1/console/overview")
        assert overview.status_code == 401, overview.text
    finally:
        app.dependency_overrides = previous_overrides


def test_console_menu_shows_settings_for_admin_override() -> None:
    async def _admin_user() -> dict[str, str]:
        return {"sub": "admin", "role": "admin"}

    app.dependency_overrides[get_current_user_optional] = _admin_user
    try:
        client = TestClient(app)
        response = client.get("/api/v1/console/menu")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["product"] == "ZEN70 Gateway Kernel"
        items = data["items"]
        route_names = [item["route_name"] for item in items]
        assert "settings" in route_names

        profile_data = client.get("/api/v1/profile").json()["data"]
        assert profile_data["console_route_names"] == [
            "dashboard",
            "nodes",
            "jobs",
            "connectors",
            "settings",
        ]
        assert profile_data["capability_keys"] == [
            "gateway.dashboard",
            "gateway.nodes",
            "gateway.jobs",
            "gateway.connectors",
            "gateway.settings",
        ]

        capability_keys = set(client.get("/api/v1/capabilities").json()["data"].keys())
        assert capability_keys == {
            "gateway.dashboard",
            "gateway.nodes",
            "gateway.jobs",
            "gateway.connectors",
            "gateway.settings",
        }
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)
