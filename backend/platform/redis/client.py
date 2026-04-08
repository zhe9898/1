"""Redis platform client composed from explicit adapters and state stores."""

from __future__ import annotations

import os
from typing import Any

try:
    import redis.asyncio as _redis_asyncio
    from redis.asyncio import Redis as _AsyncRedis
except ImportError:
    _redis_asyncio = None  # type: ignore[assignment]
    _AsyncRedis = None  # type: ignore[assignment]

from backend.platform.logging.structured import get_logger
from backend.platform.redis._shared import retry_once
from backend.platform.redis.auth_challenge_store import RedisAuthChallengeStore
from backend.platform.redis.capability_store import RedisCapabilityStore
from backend.platform.redis.constants import (
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_HARDWARE_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_SWITCH_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
    KEY_AUTH_CHALLENGE_PREFIX,
    KEY_HW_PREFIX,
    KEY_LOCK_PREFIX,
)
from backend.platform.redis.hardware_store import RedisHardwareStore
from backend.platform.redis.kv import RedisKVAdapter
from backend.platform.redis.locks import RedisLockAdapter
from backend.platform.redis.node_store import RedisNodeStore
from backend.platform.redis.pubsub import RedisPubSubAdapter
from backend.platform.redis.sorted_sets import RedisSortedSetAdapter
from backend.platform.redis.streams import RedisStreamAdapter
from backend.platform.redis.switch_store import RedisSwitchStore
from backend.platform.redis.types import Capability, HardwareState, NodeInfo, SwitchState

__all__ = [
    "Capability",
    "CHANNEL_CONNECTOR_EVENTS",
    "CHANNEL_HARDWARE_EVENTS",
    "CHANNEL_JOB_EVENTS",
    "CHANNEL_NODE_EVENTS",
    "CHANNEL_RESERVATION_EVENTS",
    "CHANNEL_SWITCH_EVENTS",
    "CHANNEL_TRIGGER_EVENTS",
    "HardwareState",
    "KEY_AUTH_CHALLENGE_PREFIX",
    "KEY_HW_PREFIX",
    "KEY_LOCK_PREFIX",
    "NodeInfo",
    "RedisClient",
    "SwitchState",
]


class RedisClient:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        password: str | None = None,
        db: int | None = None,
        request_id: str | None = None,
        username: str | None = None,
    ) -> None:
        resolved_host = host or os.getenv("REDIS_HOST")
        if not resolved_host:
            raise RuntimeError("REDIS_HOST env var is required")
        self.host = resolved_host
        self.port = port if port is not None else int(os.getenv("REDIS_PORT", "6379"))
        self.password = password if password is not None else os.getenv("REDIS_PASSWORD") or None
        self.username = username if username is not None else os.getenv("REDIS_USER") or None
        self.db = db if db is not None else int(os.getenv("REDIS_DB", "0"))
        self.max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "256"))
        self.logger = get_logger("redis_client", request_id)
        self._redis: _AsyncRedis | None = None

        self.kv = RedisKVAdapter(self)
        self.locks = RedisLockAdapter(self)
        self.pubsub = RedisPubSubAdapter(self)
        self.streams = RedisStreamAdapter(self)
        self.sorted_sets = RedisSortedSetAdapter(self)
        self.capabilities = RedisCapabilityStore(self)
        self.nodes = RedisNodeStore(self)
        self.switches = RedisSwitchStore(self)
        self.hardware = RedisHardwareStore(self)
        self.auth_challenges = RedisAuthChallengeStore(self)

    async def connect(self) -> None:
        if _redis_asyncio is None:
            raise RuntimeError("redis package not installed (pip install redis)")
        if self._redis is not None:
            return
        self._redis = _redis_asyncio.Redis(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            db=self.db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            socket_keepalive=True,
            max_connections=self.max_connections,
        )
        try:
            await self._redis.ping()
            self.logger.info("Connected to Redis")
        except Exception:
            self._redis = None
            raise

    async def close(self) -> None:
        if self._redis is None:
            return
        closer = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
        if closer is not None:
            result = closer()
            if hasattr(result, "__await__"):
                await result
        self._redis = None
        self.logger.info("Redis connection closed")

    async def ping(self) -> bool:
        if self._redis is None:
            return False
        try:
            await self._redis.ping()
            return True
        except Exception as exc:
            self.logger.debug("Redis ping failed: %s", exc)
            return False

    async def _require_connection(self) -> _AsyncRedis | None:
        return self._redis

    async def _retry_once(self, coro: Any, fallback: Any, op_name: str = "op") -> Any:
        return await retry_once(self.logger, coro, fallback, op_name)
