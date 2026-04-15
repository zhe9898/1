from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.control_plane.adapters.connectors import (
    ConnectorInvokeRequest,
    ConnectorTestRequest,
    ConnectorUpsertRequest,
    get_connector_schema,
    invoke_connector,
)
from backend.control_plane.adapters.connectors import test_connector as run_test_connector
from backend.control_plane.adapters.connectors import (
    upsert_connector,
)
from backend.control_plane.adapters.jobs import get_job_schema
from backend.control_plane.adapters.nodes import get_node_schema
from backend.tests.unit.connectors_test_support import build_connector, first_scalar_result


@pytest.mark.asyncio
async def test_get_job_schema_returns_backend_driven_contract() -> None:
    response = await get_job_schema(current_user={"sub": "admin"})

    assert response.resource == "jobs"
    assert response.title == "Jobs"
    assert response.empty_state == "No jobs match the current view."
    assert response.submit_action is not None
    assert response.submit_action.endpoint == "/v1/jobs"
    assert response.submit_action.requires_admin is False
    assert response.policies["ui_mode"] == "backend-driven"
    assert response.policies["list_query_filters"]["status"] == "status-view"
    assert response.policies["submission_security"]["baseline_scope"] == "write:jobs"
    assert response.policies["submission_security"]["privileged_scope"] == "admin:jobs"
    assert response.policies["submission_security"]["default_console_kind"] == "noop"
    assert {section.id for section in response.sections} == {"identity", "scheduling", "payload"}


@pytest.mark.asyncio
async def test_get_connector_schema_returns_backend_driven_contract() -> None:
    response = await get_connector_schema(current_user={"sub": "admin"})

    assert response.resource == "connectors"
    assert response.title == "Connectors"
    assert response.empty_state == "No connectors match the current view."
    assert response.submit_action is not None
    assert response.submit_action.endpoint == "/v1/connectors"
    assert response.submit_action.requires_admin is True
    assert response.policies["resource_mode"] == "integration-center"
    assert response.policies["list_query_filters"]["attention"] == "derived-flag"
    assert {section.id for section in response.sections} == {"identity", "runtime"}


@pytest.mark.asyncio
async def test_get_node_schema_returns_backend_driven_contract() -> None:
    response = await get_node_schema(current_user={"sub": "admin"})

    assert response.resource == "nodes"
    assert response.title == "Nodes"
    assert response.empty_state == "No nodes match the current view."
    assert response.submit_action is not None
    assert response.submit_action.endpoint == "/v1/nodes"
    assert response.policies["resource_mode"] == "fleet-management"
    assert response.policies["secret_delivery"]["field"] == "node_token"
    assert response.policies["list_query_filters"]["heartbeat_state"] == "derived"
    assert {section.id for section in response.sections} == {"identity", "runtime", "resources", "capabilities"}


@pytest.mark.asyncio
@patch("backend.control_plane.adapters.connectors.validate_connector_config", return_value={"headers": {"x-api-key": "masked"}})
@patch("backend.control_plane.adapters.connectors.check_connector_quota", new_callable=AsyncMock)
async def test_upsert_connector_returns_backend_actions(_mock_quota: AsyncMock, _mock_validate: MagicMock) -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = first_scalar_result(None)
    db.flush = AsyncMock()

    response = await upsert_connector(
        ConnectorUpsertRequest(
            connector_id="conn-new",
            name="Connector New",
            kind="http",
            endpoint="https://example.invalid",
            profile="manual",
            config={"headers": {"x-api-key": "masked"}},
        ),
        current_user={"sub": "admin", "tenant_id": "default"},
        db=db,
        redis=None,
    )

    assert {action.key for action in response.actions} == {"test", "invoke"}
    assert all(action.requires_admin is True for action in response.actions)
    assert response.attention_reason == "connector configured but not yet confirmed healthy"
    assert response.status_view.key == "configured"
    assert response.status_view.tone == "warning"


@pytest.mark.asyncio
async def test_test_connector_persists_last_test_result() -> None:
    connector = build_connector(
        tenant_id="default",
        connector_id="conn-a",
        endpoint="mqtt://broker.internal",
        status="configured",
    )
    db = AsyncMock()
    db.execute.return_value = first_scalar_result(connector)
    db.flush = AsyncMock()

    response = await run_test_connector(
        "conn-a",
        ConnectorTestRequest(timeout_ms=1000),
        current_user={"sub": "admin", "tenant_id": "default"},
        db=db,
        redis=None,
    )

    assert response.ok is True
    assert connector.last_test_ok is True
    assert connector.last_test_status == "healthy"
    assert connector.last_test_message == "connector ready"


@pytest.mark.asyncio
async def test_invoke_connector_persists_last_invoke_result() -> None:
    connector = build_connector(
        tenant_id="default",
        connector_id="conn-a",
        endpoint="https://example.invalid",
        status="healthy",
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = first_scalar_result(connector)
    db.flush = AsyncMock()

    with patch("backend.control_plane.adapters.connectors.submit_job", new=AsyncMock(return_value=SimpleNamespace(job_id="job-1"))):
        response = await invoke_connector(
            "conn-a",
            payload=ConnectorInvokeRequest(action="ping", payload={"from": "test"}, lease_seconds=30),
            current_user={"sub": "admin", "tenant_id": "default"},
            db=db,
            redis=None,
        )

    assert response.accepted is True
    assert connector.last_invoke_status == "pending"
    assert connector.last_invoke_message == "job queued"
    assert connector.last_invoke_job_id == response.job_id
