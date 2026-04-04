from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.deps import get_current_admin, get_current_user, get_db, get_tenant_db
from backend.api.main import app
from backend.api.triggers import TriggerFireRequest, TriggerUpsertRequest, fire_trigger_endpoint, upsert_trigger
from backend.models.job import Job
from backend.models.trigger import Trigger, TriggerDelivery


async def override_get_current_user() -> dict[str, str]:
    return {"id": "1", "sub": "admin", "username": "admin", "role": "admin", "tenant_id": "default"}


async def override_get_db() -> AsyncGenerator[AsyncMock, None]:
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_scalars.first.return_value = None
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute.return_value = mock_result
    yield mock_session


async def override_get_tenant_db(current_user: dict[str, str] | None = None) -> AsyncGenerator[AsyncMock, None]:
    del current_user
    async for session in override_get_db():
        yield session


app.dependency_overrides[get_current_user] = override_get_current_user
app.dependency_overrides[get_current_admin] = override_get_current_user
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_tenant_db] = override_get_tenant_db
client = TestClient(app)


def _assert_success_envelope(response: Any) -> dict[str, Any]:
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == "ZEN-OK-0"
    return payload["data"]


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _count_result(value: int) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _concurrent_counts_result(global_count: int, tenant_count: int, connector_count: int) -> MagicMock:
    result = MagicMock()
    counts_row = MagicMock()
    counts_row.global_count = global_count
    counts_row.tenant_count = tenant_count
    counts_row.connector_count = connector_count
    result.one.return_value = counts_row
    return result


def _make_trigger(**overrides: object) -> Trigger:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    trigger = Trigger(
        tenant_id="default",
        trigger_id="trigger-1",
        name="Webhook Ingest",
        description=None,
        kind="manual",
        status="active",
        config={"allow_api_fire": True},
        target={"target_kind": "job", "job_kind": "connector.invoke", "payload": {}},
        input_defaults={},
        created_by="admin",
        updated_by="admin",
        created_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(trigger, key, value)
    return trigger


def test_trigger_kinds_endpoint_lists_builtin_webhook() -> None:
    data = _assert_success_envelope(client.get("/api/v1/triggers/kinds"))
    kinds = {item["kind"] for item in data}
    assert {"manual", "cron", "webhook", "event"}.issubset(kinds)


@pytest.mark.asyncio
async def test_upsert_trigger_persists_validated_contract() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value = _scalar_result(None)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    response = await upsert_trigger(
        TriggerUpsertRequest(
            trigger_id="ingest-1",
            name="Ingest Trigger",
            kind="manual",
            config={"allow_api_fire": True},
            target={
                "target_kind": "job",
                "job_kind": "connector.invoke",
                "payload": {"connector_id": "conn-1"},
            },
            input_defaults={"action": "sync"},
        ),
        current_user={"sub": "admin", "username": "admin", "role": "admin", "tenant_id": "default"},
        db=db,
        redis=None,
    )

    assert response.trigger_id == "ingest-1"
    assert response.kind == "manual"
    assert response.target["target_kind"] == "job"
    created = db.add.call_args.args[0]
    assert isinstance(created, Trigger)
    assert created.config == {"allow_api_fire": True}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fire_trigger_dispatches_job_and_records_delivery() -> None:
    trigger = _make_trigger(
        target={"target_kind": "job", "job_kind": "connector.invoke", "payload": {"connector_id": "conn-1"}},
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(trigger),  # load trigger
        _count_result(0),  # admission count
        _concurrent_counts_result(0, 0, 0),  # global/tenant/connector concurrent counts
    ]

    response = await fire_trigger_endpoint(
        "trigger-1",
        TriggerFireRequest(
            input={"action": "ping", "payload": {"ok": True}},
            reason="manual-test",
        ),
        current_user={"sub": "admin", "username": "admin", "tenant_id": "default"},
        db=db,
        redis=None,
    )

    assert response.status == "accepted"
    assert response.target_kind == "job"
    added_types = {type(call.args[0]) for call in db.add.call_args_list}
    assert TriggerDelivery in added_types
    assert Job in added_types
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_fire_trigger_rejects_non_manual_ingress_on_fire_endpoint() -> None:
    trigger = _make_trigger(kind="webhook", config={"accepted_methods": ["POST"]})
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute.side_effect = [_scalar_result(trigger)]

    with pytest.raises(HTTPException) as exc:
        await fire_trigger_endpoint(
            "trigger-1",
            TriggerFireRequest(input={"action": "ping"}),
            current_user={"sub": "admin", "username": "admin", "tenant_id": "default"},
            db=db,
            redis=None,
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_fire_trigger_rejects_manual_ingress_when_api_fire_disabled() -> None:
    trigger = _make_trigger(kind="manual", config={"allow_api_fire": False})
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute.side_effect = [_scalar_result(trigger)]

    with pytest.raises(HTTPException) as exc:
        await fire_trigger_endpoint(
            "trigger-1",
            TriggerFireRequest(input={"action": "ping"}),
            current_user={"sub": "admin", "username": "admin", "tenant_id": "default"},
            db=db,
            redis=None,
        )

    assert exc.value.status_code == 409
