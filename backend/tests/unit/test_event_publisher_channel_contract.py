from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.platform.events.channels import CHANNEL_ROUTING_MELTDOWN, CHANNEL_SWITCH_COMMANDS
from backend.platform.events.nats_bus import NATSEventBus
from backend.platform.events.publisher import AsyncEventPublisher, SyncEventPublisher
from backend.platform.events.redis_bus import RedisEventBus
from backend.platform.redis.constants import CHANNEL_SWITCH_EVENTS


@pytest.mark.asyncio
async def test_async_publish_control_rejects_internal_subject() -> None:
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    publisher = AsyncEventPublisher(event_bus=event_bus)

    assert await publisher.publish_control(CHANNEL_SWITCH_COMMANDS, "{}") is False
    event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_publish_signal_rejects_control_subject() -> None:
    redis_client = MagicMock()
    redis_client.pubsub.publish = AsyncMock(return_value=1)
    publisher = AsyncEventPublisher(redis=redis_client)

    assert await publisher.publish_signal(CHANNEL_SWITCH_EVENTS, "{}") == 0
    redis_client.pubsub.publish.assert_not_awaited()


def test_sync_publish_signal_rejects_control_subject() -> None:
    redis_client = MagicMock()
    redis_client.pubsub.publish = MagicMock(return_value=1)
    publisher = SyncEventPublisher(redis=redis_client)

    assert publisher.publish_signal(CHANNEL_SWITCH_EVENTS, "{}") == 0
    redis_client.pubsub.publish.assert_not_called()


def test_sync_publish_control_rejects_internal_subject() -> None:
    redis_client = MagicMock()
    redis_client.pubsub.publish = MagicMock(return_value=1)
    publisher = SyncEventPublisher(settings={"event_bus_backend": "redis"}, redis=redis_client)

    assert publisher.publish_control(CHANNEL_ROUTING_MELTDOWN, "{}") is False
    redis_client.pubsub.publish.assert_not_called()


@pytest.mark.asyncio
async def test_redis_event_bus_rejects_internal_subjects() -> None:
    redis_client = MagicMock()
    redis_client.pubsub.publish = AsyncMock()
    redis_client.pubsub.session = AsyncMock()
    event_bus = RedisEventBus(redis_client)

    with pytest.raises(ValueError):
        await event_bus.publish(CHANNEL_SWITCH_COMMANDS, "{}")
    with pytest.raises(ValueError):
        await event_bus.subscribe((CHANNEL_SWITCH_COMMANDS,))

    redis_client.pubsub.publish.assert_not_awaited()
    redis_client.pubsub.session.assert_not_awaited()


@pytest.mark.asyncio
async def test_nats_event_bus_rejects_internal_subjects() -> None:
    client = MagicMock()
    client.publish = AsyncMock()
    client.subscribe = AsyncMock()
    event_bus = NATSEventBus(client)

    with pytest.raises(ValueError):
        await event_bus.publish(CHANNEL_SWITCH_COMMANDS, "{}")
    with pytest.raises(ValueError):
        await event_bus.subscribe((CHANNEL_SWITCH_COMMANDS,))

    client.publish.assert_not_awaited()
    client.subscribe.assert_not_awaited()
