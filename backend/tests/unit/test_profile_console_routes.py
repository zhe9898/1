from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_optional
from backend.control_plane.app.entrypoint import app


def _assert_identity_cache_headers(response) -> None:
    assert response.headers.get("cache-control") == "no-store, private"
    assert response.headers.get("pragma") == "no-cache"
    assert response.headers.get("expires") == "0"
    vary = response.headers.get("vary", "").lower()
    assert "cookie" in vary


def test_profile_endpoint_reports_public_profile(monkeypatch) -> None:
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        monkeypatch.setenv("GATEWAY_PROFILE", "gateway-kernel")
        monkeypatch.delenv("GATEWAY_PACKS", raising=False)
        client = TestClient(app)

        response = client.get("/api/v1/profile")
        assert response.status_code == 200, response.text
        _assert_identity_cache_headers(response)

        envelope = response.json()
        assert envelope["code"] == "ZEN-OK-0"
        data = envelope["data"]
        assert data["product"] == "ZEN70 Gateway Kernel"
        assert data["profile"] == "gateway-kernel"
        assert data["runtime_profile"] is None
        assert "nodes" in data["router_names"]
        assert "jobs" in data["router_names"]
        assert data["console_route_names"] == [
            "dashboard",
            "nodes",
            "jobs",
            "connectors",
            "triggers",
            "reservations",
            "evaluations",
        ]
        assert data["capability_keys"] == [
            "platform.capabilities.query",
            "control.nodes.manage",
            "control.jobs.schedule",
            "control.connectors.invoke",
            "control.triggers.manage",
            "control.reservations.manage",
            "control.evaluations.manage",
        ]
        assert data["requested_pack_keys"] == []
        assert data["resolved_pack_keys"] == []
        assert {item["pack_key"] for item in data["packs"]} == {
            "iot-pack",
            "ops-pack",
            "media-pack",
            "health-pack",
            "vector-pack",
        }
        pack_map = {item["pack_key"]: item for item in data["packs"]}
        assert pack_map["health-pack"]["delivery_stage"] == "runtime-present"
        assert pack_map["vector-pack"]["delivery_stage"] == "runtime-present"
        assert pack_map["iot-pack"]["selector"] == {
            "required_capabilities": ["iot.adapter"],
            "target_zone": "home",
            "target_executors": [],
            "target_executor_contracts": [],
        }
        assert pack_map["health-pack"]["selector"] == {
            "required_capabilities": ["health.ingest"],
            "target_zone": "mobile",
            "target_executors": ["swift-native", "kotlin-native"],
            "target_executor_contracts": ["process"],
        }
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
        _assert_identity_cache_headers(response)

        data = response.json()["data"]
        assert data["profile"] == "gateway-kernel"
        assert data["runtime_profile"] is None
        assert data["requested_pack_keys"] == ["iot-pack", "health-pack"]
        assert data["resolved_pack_keys"] == ["iot-pack", "health-pack"]
        pack_map = {item["pack_key"]: item for item in data["packs"]}
        assert pack_map["iot-pack"]["selected"] is True
        assert pack_map["health-pack"]["selected"] is True
        assert pack_map["health-pack"]["delivery_stage"] == "runtime-present"
        assert pack_map["iot-pack"]["router_names"] == ["iot", "scenes", "scheduler"]
        assert pack_map["iot-pack"]["services"] == ["mosquitto"]
        assert pack_map["health-pack"]["services"] == []
        assert pack_map["health-pack"]["selector"]["target_executors"] == ["swift-native", "kotlin-native"]
        assert pack_map["health-pack"]["selector"]["target_executor_contracts"] == ["process"]
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
        _assert_identity_cache_headers(response)

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
        _assert_identity_cache_headers(cap_response)
        capability_keys = set(cap_response.json()["data"].keys())
        assert capability_keys == {
            "platform.capabilities.query",
            "control.nodes.manage",
            "control.jobs.schedule",
            "control.connectors.invoke",
            "control.triggers.manage",
            "control.reservations.manage",
            "control.evaluations.manage",
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
        _assert_identity_cache_headers(response)
        data = response.json()["data"]
        assert data["product"] == "ZEN70 Gateway Kernel"
        items = data["items"]
        route_names = [item["route_name"] for item in items]
        assert "settings" in route_names

        profile_response = client.get("/api/v1/profile")
        _assert_identity_cache_headers(profile_response)
        profile_data = profile_response.json()["data"]
        assert profile_data["runtime_profile"] == "gateway-kernel"
        assert profile_data["console_route_names"] == [
            "dashboard",
            "nodes",
            "jobs",
            "connectors",
            "triggers",
            "reservations",
            "evaluations",
            "settings",
        ]
        assert profile_data["capability_keys"] == [
            "platform.capabilities.query",
            "control.nodes.manage",
            "control.jobs.schedule",
            "control.connectors.invoke",
            "control.triggers.manage",
            "control.reservations.manage",
            "control.evaluations.manage",
            "platform.settings.manage",
        ]

        capabilities_response = client.get("/api/v1/capabilities")
        _assert_identity_cache_headers(capabilities_response)
        capability_keys = set(capabilities_response.json()["data"].keys())
        assert capability_keys == {
            "platform.capabilities.query",
            "control.nodes.manage",
            "control.jobs.schedule",
            "control.connectors.invoke",
            "control.triggers.manage",
            "control.reservations.manage",
            "control.evaluations.manage",
            "platform.settings.manage",
        }
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)


def test_console_menu_shows_settings_for_superadmin_override() -> None:
    async def _superadmin_user() -> dict[str, str]:
        return {"sub": "superadmin", "role": "superadmin"}

    app.dependency_overrides[get_current_user_optional] = _superadmin_user
    try:
        client = TestClient(app)
        menu_response = client.get("/api/v1/console/menu")
        _assert_identity_cache_headers(menu_response)
        menu_data = menu_response.json()["data"]
        assert "settings" in [item["route_name"] for item in menu_data["items"]]
        surfaces_response = client.get("/api/v1/console/surfaces")
        _assert_identity_cache_headers(surfaces_response)
        surfaces_data = surfaces_response.json()["data"]
        assert "settings" in [item["route_name"] for item in surfaces_data["surfaces"]]
        profile_response = client.get("/api/v1/profile")
        _assert_identity_cache_headers(profile_response)
        profile_data = profile_response.json()["data"]
        assert profile_data["runtime_profile"] == "gateway-kernel"
        assert "settings" in profile_data["console_route_names"]
        assert "platform.settings.manage" in profile_data["capability_keys"]
    finally:
        app.dependency_overrides.pop(get_current_user_optional, None)
