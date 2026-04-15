from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.platform.events.channels import CHANNEL_ROUTING_MELTDOWN
from backend.platform.events.subscriber import AsyncInternalSignalSubscriber, SyncInternalSignalSubscriber


@pytest.mark.asyncio
async def test_async_internal_signal_subscriber_wraps_pubsub_messages() -> None:
    pubsub = SimpleNamespace(
        subscribe=AsyncMock(),
        unsubscribe=AsyncMock(),
        close=AsyncMock(),
        get_message=AsyncMock(return_value={"type": "message", "channel": CHANNEL_ROUTING_MELTDOWN, "data": '{"x":1}'}),
    )
    redis_client = SimpleNamespace(pubsub=SimpleNamespace(session=AsyncMock(return_value=pubsub)))

    subscription = await AsyncInternalSignalSubscriber(redis_client).subscribe((CHANNEL_ROUTING_MELTDOWN,))
    message = await subscription.get_message()
    await subscription.close()

    assert message is not None
    assert message.subject == CHANNEL_ROUTING_MELTDOWN
    assert message.data == '{"x":1}'
    pubsub.subscribe.assert_awaited_once_with(CHANNEL_ROUTING_MELTDOWN)
    pubsub.unsubscribe.assert_awaited_once_with(CHANNEL_ROUTING_MELTDOWN)
    pubsub.close.assert_awaited_once()


def test_sync_internal_signal_subscriber_wraps_pubsub_messages() -> None:
    pubsub = SimpleNamespace(
        subscribe=MagicMock(),
        unsubscribe=MagicMock(),
        close=MagicMock(),
        get_message=MagicMock(return_value={"type": "message", "channel": CHANNEL_ROUTING_MELTDOWN, "data": '{"x":1}'}),
    )
    redis_client = SimpleNamespace(pubsub=SimpleNamespace(session=MagicMock(return_value=pubsub)))

    subscription = SyncInternalSignalSubscriber(redis_client).subscribe((CHANNEL_ROUTING_MELTDOWN,))
    message = subscription.get_message()
    subscription.close()

    assert message is not None
    assert message.subject == CHANNEL_ROUTING_MELTDOWN
    assert message.data == '{"x":1}'
    pubsub.subscribe.assert_called_once_with(CHANNEL_ROUTING_MELTDOWN)
    pubsub.unsubscribe.assert_called_once_with(CHANNEL_ROUTING_MELTDOWN)
    pubsub.close.assert_called_once()


def test_internal_signal_subscriber_rejects_non_internal_subject() -> None:
    redis_client = SimpleNamespace(pubsub=SimpleNamespace(session=MagicMock()))

    with pytest.raises(ValueError):
        SyncInternalSignalSubscriber(redis_client).subscribe(("switch:events",))
