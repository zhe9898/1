from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user, get_db, get_tenant_db
from backend.api.main import app


async def override_get_current_user() -> dict[str, str]:
    return {"id": "1", "username": "admin", "role": "admin", "tenant_id": "default"}


async def override_get_superadmin_user() -> dict[str, str]:
    return {"id": "1", "username": "root", "role": "superadmin", "tenant_id": "default"}


async def override_get_db() -> AsyncGenerator[AsyncMock, None]:
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_scalar_result = MagicMock()
    mock_scalar_result.all.return_value = []
    mock_scalar_result.first.return_value = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.all.return_value = []
    mock_result.scalars.return_value = mock_scalar_result

    mock_session.execute.return_value = mock_result
    yield mock_session


async def override_get_tenant_db(
    current_user: dict[str, str] | None = None,
) -> AsyncGenerator[AsyncMock, None]:
    """Bypass RLS assert_rls_ready by providing a mock session directly."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_scalar_result = MagicMock()
    mock_scalar_result.all.return_value = []
    mock_scalar_result.first.return_value = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.all.return_value = []
    mock_result.scalars.return_value = mock_scalar_result

    mock_session.execute.return_value = mock_result
    yield mock_session


app.dependency_overrides[get_current_user] = override_get_current_user
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_tenant_db] = override_get_tenant_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_profile_env_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_tenant_db] = override_get_tenant_db
    monkeypatch.setenv("GATEWAY_PROFILE", "gateway-kernel")
    monkeypatch.delenv("GATEWAY_PACKS", raising=False)


@pytest.fixture(autouse=True)
def mock_service_readiness() -> Any:
    from backend.shared_state import service_readiness

    service_readiness.update({"postgres": True, "redis": True, "jellyfin": True, "jellyseerr": True})
    yield
    service_readiness.clear()


def _assert_success_envelope(response: Any) -> dict[str, Any]:
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "ZEN-OK-0"
    assert payload["message"] == "ok"
    assert "data" in payload
    assert "recovery_hint" not in payload
    assert "details" not in payload
    return payload


def test_health_skips_envelope() -> None:
    with patch("backend.api.main._check_postgres_async", return_value="ok"):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "code" not in data or data.get("code") != "ZEN-OK-0"
        assert "status" in data
        assert "version" in data


def test_auth_sys_status_has_envelope() -> None:
    response = client.get("/api/v1/auth/sys/status")
    payload = _assert_success_envelope(response)
    assert payload["data"]["initialized"] is False


def test_capabilities_has_envelope() -> None:
    response = client.get("/api/v1/capabilities")
    _assert_success_envelope(response)


def test_settings_system_has_envelope() -> None:
    app.dependency_overrides[get_current_user] = override_get_superadmin_user
    try:
        response = client.get("/api/v1/settings/system-info")
        _assert_success_envelope(response)
    finally:
        app.dependency_overrides[get_current_user] = override_get_current_user


def test_settings_schema_has_envelope() -> None:
    app.dependency_overrides[get_current_user] = override_get_superadmin_user
    try:
        response = client.get("/api/v1/settings/schema")
        data = _assert_success_envelope(response)["data"]
        section_ids = {section["id"] for section in data["sections"]}
        assert {"profile", "network", "connectors", "security"}.issubset(section_ids)
        profile_section = next(section for section in data["sections"] if section["id"] == "profile")
        field_keys = {field["key"] for field in profile_section["fields"]}
        assert {"requested_packs", "resolved_packs", "available_packs"}.issubset(field_keys)
    finally:
        app.dependency_overrides[get_current_user] = override_get_current_user


def test_profile_has_envelope_and_kernel_product() -> None:
    response = client.get("/api/v1/profile")
    data = _assert_success_envelope(response)["data"]
    assert data["product"] == "ZEN70 Gateway Kernel"
    assert data["profile"] == "gateway-kernel"
    assert data["runtime_profile"] == "gateway-kernel"
    assert "requested_pack_keys" in data
    assert "resolved_pack_keys" in data
    assert isinstance(data["packs"], list)


def test_console_menu_has_envelope_and_control_plane_entries() -> None:
    response = client.get("/api/v1/console/menu")
    data = _assert_success_envelope(response)["data"]
    assert data["product"] == "ZEN70 Gateway Kernel"
    route_names = {item["route_name"] for item in data["items"]}
    assert {"dashboard", "nodes", "jobs", "connectors", "triggers"}.issubset(route_names)
    assert "settings" not in route_names


def test_console_overview_has_envelope() -> None:
    response = client.get("/api/v1/console/overview")
    data = _assert_success_envelope(response)["data"]
    assert data["product"] == "ZEN70 Gateway Kernel"
    assert data["profile"] == "gateway-kernel"
    assert data["runtime_profile"] == "gateway-kernel"
    assert isinstance(data["attention"], list)
    assert {"nodes", "jobs", "connectors"}.issubset(data.keys())


def test_nodes_list_has_envelope() -> None:
    response = client.get("/api/v1/nodes")
    data = _assert_success_envelope(response)
    assert isinstance(data["data"], list)


def test_jobs_list_has_envelope() -> None:
    response = client.get("/api/v1/jobs")
    data = _assert_success_envelope(response)
    assert isinstance(data["data"], list)


def test_connectors_list_has_envelope() -> None:
    response = client.get("/api/v1/connectors")
    data = _assert_success_envelope(response)
    assert isinstance(data["data"], list)
