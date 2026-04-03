from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.push import PushKeys, PushPayload, PushSubscribeInput, subscribe_push
from backend.api.push import test_trigger_push as trigger_push_notification
from backend.models.user import PushSubscription


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_all_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


@pytest.mark.asyncio
async def test_subscribe_push_looks_up_existing_endpoint_with_tenant_scope() -> None:
    session = AsyncMock()
    session.flush = AsyncMock()
    session.execute.return_value = _scalar_result(None)
    session.add = MagicMock()

    response = await subscribe_push(
        PushSubscribeInput(
            endpoint="https://push.example/subscription-1",
            keys=PushKeys(p256dh="tenant-a-p256dh", auth="tenant-a-auth"),
            user_agent="tenant-a-agent",
        ),
        current_user={"sub": "7", "tenant_id": "tenant-a"},
        session=session,
    )

    assert response["status"] == "ok"
    stmt = session.execute.await_args.args[0]
    rendered = str(stmt)
    assert "push_subscriptions.tenant_id" in rendered
    assert "push_subscriptions.endpoint" in rendered
    created = session.add.call_args.args[0]
    assert created.tenant_id == "tenant-a"
    assert created.user_id == 7


@pytest.mark.asyncio
async def test_test_trigger_push_reads_subscriptions_for_current_tenant_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.api.push.VAPID_PRIVATE_KEY", "test-private-key")
    monkeypatch.setattr("backend.api.push.asyncio.to_thread", AsyncMock(return_value=None))

    tenant_scoped_sub = PushSubscription(
        tenant_id="tenant-a",
        user_id=7,
        endpoint="https://push.example/subscription-1",
        p256dh="tenant-a-p256dh",
        auth="tenant-a-auth",
        user_agent="tenant-a-agent",
    )
    session = AsyncMock()
    session.flush = AsyncMock()
    session.execute.return_value = _scalars_all_result([tenant_scoped_sub])

    response = await trigger_push_notification(
        payload=PushPayload(title="Ping", body="Test"),
        current_user={"sub": "7", "tenant_id": "tenant-a"},
        session=session,
    )

    stmt = session.execute.await_args.args[0]
    rendered = str(stmt)
    assert "push_subscriptions.tenant_id" in rendered
    assert "push_subscriptions.user_id" in rendered
    assert response["dispatched"] == 1
    assert response["failed"] == 0


@pytest.mark.asyncio
async def test_test_trigger_push_isolates_non_webpush_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.api.push.VAPID_PRIVATE_KEY", "test-private-key")
    monkeypatch.setattr("backend.api.push.asyncio.to_thread", AsyncMock(side_effect=RuntimeError("network down")))

    tenant_scoped_sub = PushSubscription(
        tenant_id="tenant-a",
        user_id=7,
        endpoint="https://push.example/subscription-1",
        p256dh="tenant-a-p256dh",
        auth="tenant-a-auth",
        user_agent="tenant-a-agent",
    )
    session = AsyncMock()
    session.flush = AsyncMock()
    session.execute.return_value = _scalars_all_result([tenant_scoped_sub])

    response = await trigger_push_notification(
        payload=PushPayload(title="Ping", body="Test"),
        current_user={"sub": "7", "tenant_id": "tenant-a"},
        session=session,
    )

    assert response["dispatched"] == 0
    assert response["failed"] == 1
