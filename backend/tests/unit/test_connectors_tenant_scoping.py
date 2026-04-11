from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.control_plane.adapters.connectors import (
    ConnectorInvokeRequest,
    ConnectorTestRequest,
    ConnectorUpsertRequest,
    invoke_connector,
    list_connectors,
)
from backend.control_plane.adapters.connectors import test_connector as connector_test_endpoint
from backend.control_plane.adapters.connectors import (
    upsert_connector,
)
from backend.extensions.connector_secret_service import ConnectorSecretService
from backend.tests.unit.connectors_test_support import build_connector, first_scalar_result


def _render_sql(statement: object) -> str:
    return str(statement)


@pytest.mark.asyncio
@patch("backend.control_plane.adapters.connectors.validate_connector_config", return_value={"headers": {"x-api-key": "top-secret"}})
@patch("backend.control_plane.adapters.connectors.check_connector_quota", new_callable=AsyncMock)
async def test_upsert_connector_scopes_lookup_to_current_tenant(_mock_quota: AsyncMock, _mock_validate: MagicMock) -> None:
    db = AsyncMock()
    db.execute.return_value = first_scalar_result(None)
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
            config={"headers": {"x-api-key": "top-secret"}},
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
    assert created.config["format"] == ConnectorSecretService.ENVELOPE_FORMAT
    assert created.config["masked"] == {"headers": {"x-api-key": "********"}}
    assert "headers" not in created.config
    assert response.connector_id == "connector-a"
    assert response.config == {"headers": {"x-api-key": "********"}}


@pytest.mark.asyncio
async def test_invoke_connector_scopes_lookup_to_current_tenant() -> None:
    db = AsyncMock()
    db.execute.return_value = first_scalar_result(build_connector())
    db.flush = AsyncMock()
    db.add = MagicMock()

    with (
        patch("backend.control_plane.adapters.connectors.submit_job", new=AsyncMock(return_value=SimpleNamespace(job_id="job-1"))),
        patch("backend.control_plane.adapters.connectors.publish_control_event", new=AsyncMock()) as publish_event,
    ):
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
    published_payload = publish_event.await_args.args[2]
    assert published_payload["connector_action"] == "ping"
    assert "action" not in published_payload


@pytest.mark.asyncio
async def test_list_connectors_scopes_query_to_current_tenant() -> None:
    db = AsyncMock()
    db.execute.return_value = first_scalar_result(build_connector())

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
    db.execute.return_value = first_scalar_result(None)

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
    db.execute.return_value = first_scalar_result(build_connector(status="error"))

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
    db.execute.return_value = first_scalar_result(None)

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


@pytest.mark.asyncio
@patch("backend.control_plane.adapters.connectors.validate_connector_config", return_value={})
@patch("backend.control_plane.adapters.connectors.check_connector_quota", new_callable=AsyncMock)
async def test_upsert_connector_rejects_private_ip_endpoint(_mock_quota: AsyncMock, _mock_validate: MagicMock) -> None:
    db = AsyncMock()
    db.execute.return_value = first_scalar_result(None)
    db.flush = AsyncMock()
    db.add = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await upsert_connector(
            ConnectorUpsertRequest(
                connector_id="connector-a",
                name="Connector A",
                kind="http",
                status="configured",
                endpoint="http://127.0.0.1:8080",
                profile="manual",
                config={},
            ),
            current_user={"sub": "admin", "tenant_id": "tenant-a"},
            db=db,
            redis=None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ZEN-CONN-4002"


@pytest.mark.asyncio
@patch("backend.control_plane.adapters.connectors.check_connector_quota", new_callable=AsyncMock)
@patch("backend.control_plane.adapters.connectors.validate_connector_config", side_effect=ValueError("invalid connector config"))
async def test_upsert_connector_masks_sensitive_config_in_validation_error(
    _mock_validate: MagicMock,
    _mock_quota: AsyncMock,
) -> None:
    db = AsyncMock()
    db.execute.return_value = first_scalar_result(None)
    db.flush = AsyncMock()
    db.add = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await upsert_connector(
            ConnectorUpsertRequest(
                connector_id="connector-a",
                name="Connector A",
                kind="http",
                status="configured",
                endpoint="https://example.test",
                profile="manual",
                config={"headers": {"x-api-key": "top-secret"}, "client_secret": "raw-secret"},
            ),
            current_user={"sub": "admin", "tenant_id": "tenant-a"},
            db=db,
            redis=None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ZEN-CONN-4001"
    assert exc_info.value.detail["details"]["config"] == {
        "headers": {"x-api-key": "********"},
        "client_secret": "********",
    }
