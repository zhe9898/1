from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

from backend.platform.redis.client import RedisClient


def make_client(*, host: str = "localhost") -> RedisClient:
    with patch.dict(os.environ, {"REDIS_HOST": host}):
        return RedisClient()


def make_connected_client() -> RedisClient:
    client = make_client()
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    client._redis = mock_redis
    return client
