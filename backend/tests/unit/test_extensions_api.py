from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from backend.control_plane.adapters.deps import get_current_user, get_db, get_tenant_db
from backend.control_plane.app.entrypoint import app


async def override_get_current_user() -> dict[str, str]:
    return {"id": "1", "username": "admin", "role": "admin", "tenant_id": "default"}


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


async def override_get_tenant_db(current_user: dict[str, str] | None = None) -> AsyncGenerator[AsyncMock, None]:
    del current_user
    async for session in override_get_db():
        yield session


app.dependency_overrides[get_current_user] = override_get_current_user
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_tenant_db] = override_get_tenant_db
client = TestClient(app)


def _assert_success_envelope(response: Any) -> dict[str, Any]:
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "ZEN-OK-0"
    assert payload["message"] == "ok"
    assert "data" in payload
    return payload["data"]


def test_extensions_list_has_envelope() -> None:
    data = _assert_success_envelope(client.get("/api/v1/extensions"))
    assert isinstance(data, list)
    assert any(item["extension_id"] == "zen70.core" for item in data)


def test_extensions_job_kinds_exposes_connector_invoke_metadata() -> None:
    data = _assert_success_envelope(client.get("/api/v1/extensions/job-kinds"))
    connector_invoke = next(item for item in data if item["kind"] == "connector.invoke")
    assert connector_invoke["metadata"]["extension_id"] == "zen70.core"


def test_extensions_get_single_job_kind_endpoint() -> None:
    data = _assert_success_envelope(client.get("/api/v1/extensions/job-kinds/connector.invoke"))
    assert data["kind"] == "connector.invoke"
    assert data["metadata"]["extension_id"] == "zen70.core"


def test_extensions_get_single_connector_kind_endpoint() -> None:
    data = _assert_success_envelope(client.get("/api/v1/extensions/connector-kinds/http"))
    assert data["kind"] == "http"
    assert data["metadata"]["extension_id"] == "zen70.core"


def test_extensions_workflow_template_render_endpoint() -> None:
    data = _assert_success_envelope(
        client.post(
            "/api/v1/extensions/workflow-templates/ops.http-healthcheck/render",
            json={"parameters": {"target": "https://example.com/health", "expected_status": 204}},
        )
    )
    assert data["template_id"] == "ops.http-healthcheck"
    assert data["steps"][0]["payload"]["target"] == "https://example.com/health"
    assert data["steps"][0]["payload"]["expected_status"] == 204
