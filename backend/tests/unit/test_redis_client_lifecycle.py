from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests.unit.redis_test_utils import make_client, make_connected_client


class TestConnectClose:
    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        client = make_client()
        mock_redis_instance = AsyncMock()
        mock_redis_instance.ping = AsyncMock(return_value=True)

        with patch("backend.platform.redis.client.redis") as mock_redis_module:
            mock_redis_module.Redis = MagicMock(return_value=mock_redis_instance)
            await client.connect()
            _, kwargs = mock_redis_module.Redis.call_args
            assert kwargs["max_connections"] == client.max_connections

        assert client._redis is not None

    @pytest.mark.asyncio
    async def test_connect_failure_sets_none(self) -> None:
        client = make_client()
        mock_redis_instance = AsyncMock()
        mock_redis_instance.ping = AsyncMock(side_effect=OSError("refused"))

        with patch("backend.platform.redis.client.redis") as mock_redis_module:
            mock_redis_module.Redis = MagicMock(return_value=mock_redis_instance)
            with pytest.raises(OSError, match="refused"):
                await client.connect()

        assert client._redis is None

    @pytest.mark.asyncio
    async def test_close_clears_redis(self) -> None:
        client = make_connected_client()
        client._redis.aclose = AsyncMock()  # type: ignore[union-attr]
        await client.close()
        assert client._redis is None

    @pytest.mark.asyncio
    async def test_double_connect_noop(self) -> None:
        client = make_connected_client()
        original = client._redis
        await client.connect()
        assert client._redis is original

    @pytest.mark.asyncio
    async def test_ping_false_when_not_connected(self) -> None:
        client = make_client()
        assert await client.ping() is False
