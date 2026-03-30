from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.connectors import (
    ConnectorInvokeRequest,
    ConnectorTestRequest,
    ConnectorUpsertRequest,
    get_connector_schema,
    invoke_connector,
)
from backend.api.connectors import test_connector as run_test_connector
from backend.api.connectors import (
    upsert_connector,
)
from backend.api.jobs import get_job_schema
from backend.api.nodes import get_node_schema
from backend.models.connector import Connector


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _result_first(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _connector(**overrides: object) -> Connector:
    now = _utcnow()
    connector = Connector(
        connector_id="conn-a",
        name="Connector A",
        kind="http",
        status="configured",
        endpoint="https://example.invalid",
        profile="manual",
        config={},
        last_test_ok=None,
        last_test_status=None,
        last_test_message=None,
        last_test_at=None,
        last_invoke_status=None,
        last_invoke_message=None,
        last_invoke_job_id=None,
        last_invoke_at=None,
        created_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


@pytest.mark.asyncio
async def test_get_job_schema_returns_backend_driven_contract() -> None:
    response = await get_job_schema(current_user={"sub": "admin"})

    assert response.resource == "jobs"
    assert response.title == "Jobs"
    assert response.empty_state == "No jobs match the current view."
    assert response.submit_action is not None
    assert response.submit_action.endpoint == "/v1/jobs"
    assert response.policies["ui_mode"] == "backend-driven"
    assert response.policies["list_query_filters"]["status"] == "status-view"
    assert {section.id for section in response.sections} == {"identity", "scheduling", "payload"}


@pytest.mark.asyncio
async def test_get_connector_schema_returns_backend_driven_contract() -> None:
    response = await get_connector_schema(current_user={"sub": "admin"})

    assert response.resource == "connectors"
    assert response.title == "Connectors"
    assert response.empty_state == "No connectors match the current view."
    assert response.submit_action is not None
    assert response.submit_action.endpoint == "/v1/connectors"
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
async def test_upsert_connector_returns_backend_actions() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result_first(None)
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
        current_user={"sub": "admin"},
        db=db,
        redis=None,
    )

    assert {action.key for action in response.actions} == {"test", "invoke"}
    assert response.attention_reason == "connector configured but not yet confirmed healthy"
    assert response.status_view.key == "configured"
    assert response.status_view.tone == "warning"


@pytest.mark.asyncio
async def test_test_connector_persists_last_test_result() -> None:
    connector = _connector(endpoint="mqtt://broker.internal")
    db = AsyncMock()
    db.execute.return_value = _result_first(connector)
    db.flush = AsyncMock()

    response = await run_test_connector(
        "conn-a",
        ConnectorTestRequest(timeout_ms=1000),
        current_user={"sub": "admin"},
        db=db,
        redis=None,
    )

    assert response.ok is True
    assert connector.last_test_ok is True
    assert connector.last_test_status == "healthy"
    assert connector.last_test_message == "connector ready"


@pytest.mark.asyncio
async def test_invoke_connector_persists_last_invoke_result() -> None:
    connector = _connector(status="healthy")
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _result_first(connector)
    db.flush = AsyncMock()

    response = await invoke_connector(
        "conn-a",
        payload=ConnectorInvokeRequest(action="ping", payload={"from": "test"}, lease_seconds=30),
        current_user={"sub": "admin"},
        db=db,
        redis=None,
    )

    assert response.accepted is True
    assert connector.last_invoke_status == "pending"
    assert connector.last_invoke_message == "job queued"
    assert connector.last_invoke_job_id == response.job_id
