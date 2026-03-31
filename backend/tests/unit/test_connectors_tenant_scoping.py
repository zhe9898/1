from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.api.connectors import (
    ConnectorInvokeRequest,
    ConnectorTestRequest,
    ConnectorUpsertRequest,
    invoke_connector,
    list_connectors,
)
from backend.api.connectors import test_connector as connector_test_endpoint
from backend.api.connectors import (
    upsert_connector,
)
from backend.models.connector import Connector


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    scalars.all.return_value = [value] if value is not None else []
    result.scalars.return_value = scalars
    return result


def _render_sql(statement: object) -> str:
    return str(statement)


def _connector(**overrides: object) -> Connector:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    connector = Connector(
        tenant_id="tenant-a",
        connector_id="connector-a",
        name="Connector A",
        kind="http",
        status="healthy",
        endpoint="https://example.test",
        profile="manual",
        config={},
        created_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


@pytest.mark.asyncio
@patch("backend.api.connectors.validate_connector_config", return_value={})
@patch("backend.api.connectors.check_connector_quota", new_callable=AsyncMock)
async def test_upsert_connector_scopes_lookup_to_current_tenant(_mock_quota: AsyncMock, _mock_validate: MagicMock) -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)
    db.flush = AsyncMock()
    db.add = MagicMock()

    response = await upsert_connector(
        ConnectorUpsertRequest(
            connector_id="connector-a",
            name="Connector A",
            kind="http",
            status="configured",
            endpoint="https://example.test",
            profile="manual",
            config={},
        ),
        current_user={"sub": "admin", "tenant_id": "tenant-a"},
        db=db,
        redis=None,
    )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "connectors.tenant_id" in rendered
    assert "connectors.connector_id" in rendered
    created = db.add.call_args.args[0]
    assert created.tenant_id == "tenant-a"
    assert response.connector_id == "connector-a"


@pytest.mark.asyncio
async def test_invoke_connector_scopes_lookup_to_current_tenant() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(_connector())
    db.flush = AsyncMock()
    db.add = MagicMock()

    response = await invoke_connector(
        "connector-a",
        ConnectorInvokeRequest(action="ping", payload={}),
        current_user={"sub": "admin", "tenant_id": "tenant-a"},
        db=db,
        redis=None,
    )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "connectors.tenant_id" in rendered
    assert "connectors.connector_id" in rendered
    assert response.accepted is True


@pytest.mark.asyncio
async def test_list_connectors_scopes_query_to_current_tenant() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(_connector())

    response = await list_connectors(
        connector_id="connector-a",
        current_user={"sub": "admin", "tenant_id": "tenant-a"},
        db=db,
    )

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "connectors.tenant_id" in rendered
    assert "connectors.connector_id" in rendered
    assert len(response) == 1


@pytest.mark.asyncio
async def test_invoke_connector_missing_connector_uses_zen_error_contract() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)

    with pytest.raises(HTTPException) as exc_info:
        await invoke_connector(
            "missing",
            ConnectorInvokeRequest(action="ping", payload={}),
            current_user={"sub": "admin", "tenant_id": "tenant-a"},
            db=db,
            redis=None,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "ZEN-CONN-4040"


@pytest.mark.asyncio
async def test_invoke_connector_not_ready_uses_zen_error_contract() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(_connector(status="error"))

    with pytest.raises(HTTPException) as exc_info:
        await invoke_connector(
            "connector-a",
            ConnectorInvokeRequest(action="ping", payload={}),
            current_user={"sub": "admin", "tenant_id": "tenant-a"},
            db=db,
            redis=None,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "ZEN-CONN-4090"


@pytest.mark.asyncio
async def test_test_connector_missing_connector_uses_zen_error_contract() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)

    with pytest.raises(HTTPException) as exc_info:
        await connector_test_endpoint(
            "missing",
            ConnectorTestRequest(),
            current_user={"sub": "admin", "tenant_id": "tenant-a"},
            db=db,
            redis=None,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "ZEN-CONN-4040"
