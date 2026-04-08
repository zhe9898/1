from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.tests.unit.redis_test_utils import make_client, make_connected_client


class TestKVDefaults:
    @pytest.mark.asyncio
    async def test_safe_defaults_when_not_connected(self) -> None:
        client = make_client()
        assert await client.kv.get("key") is None
        assert await client.kv.set("key", "value") is None
        assert await client.kv.setex("key", 60, "value") is None
        assert await client.kv.delete("key") == 0
        assert await client.kv.incr("key") == 0
        assert await client.kv.expire("key", 60) is False
        assert await client.kv.get_many(["a", "b"]) == [None, None]

    @pytest.mark.asyncio
    async def test_get_many_uses_pipeline(self) -> None:
        client = make_connected_client()
        mock_pipe = AsyncMock()
        mock_pipe.get = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=["one", None])
        client._redis.pipeline = MagicMock(return_value=mock_pipe)  # type: ignore[method-assign, union-attr]

        assert await client.kv.get_many(["a", "b"]) == ["one", None]
        assert mock_pipe.get.call_count == 2


class TestLocksAndPubSub:
    @pytest.mark.asyncio
    async def test_lock_lifecycle(self) -> None:
        client = make_connected_client()
        client._redis.set = AsyncMock(return_value=True)  # type: ignore[method-assign, union-attr]
        assert await client.locks.acquire("test-lock", ttl=10) is True

        client._redis.exists = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await client.locks.exists("test-lock") is True

        client._redis.set = AsyncMock(return_value=None)  # type: ignore[method-assign, union-attr]
        assert await client.locks.acquire("test-lock", ttl=10) is False

        client._redis.delete = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await client.locks.release("test-lock") is True

    @pytest.mark.asyncio
    async def test_pubsub_publish_and_session(self) -> None:
        client = make_connected_client()
        client._redis.publish = AsyncMock(return_value=2)  # type: ignore[method-assign, union-attr]
        assert await client.pubsub.publish("channel", "payload") == 2

        raw_pubsub = AsyncMock()
        client._redis.pubsub = MagicMock(return_value=raw_pubsub)  # type: ignore[method-assign, union-attr]
        session = await client.pubsub.session()
        assert session is not None
        await session.subscribe("channel")
        raw_pubsub.subscribe.assert_awaited_once_with("channel")
