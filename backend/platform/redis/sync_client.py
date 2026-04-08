from __future__ import annotations

import os
from typing import Any

try:
    import redis
except ImportError:
    redis = None  # type: ignore[assignment]

from backend.platform.logging.structured import get_logger
from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, SyncRedisComponent
from backend.platform.redis.constants import KEY_LOCK_PREFIX


class SyncRedisKVAdapter(SyncRedisComponent):
    def get(self, key: str) -> str | None:
        connection = self._connection()
        if connection is None:
            return None
        try:
            value = connection.get(key)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.get failed for %s: %s", key, exc, exc_info=True)
            return None
        return None if value is None else str(value)

    def set(self, key: str, value: str | bytes | int | float, **kwargs: Any) -> object:
        connection = self._connection()
        if connection is None:
            return None
        try:
            return connection.set(key, value, **kwargs)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.set failed for %s: %s", key, exc, exc_info=True)
            return None

    def setex(self, key: str, ttl_seconds: int, value: str | bytes | int | float) -> object:
        connection = self._connection()
        if connection is None:
            return None
        try:
            return connection.setex(key, ttl_seconds, value)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.setex failed for %s: %s", key, exc, exc_info=True)
            return None

    def delete(self, *keys: str) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.delete(*keys))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.delete failed for %s: %s", keys, exc, exc_info=True)
            return 0

    def exists(self, key: str) -> bool:
        connection = self._connection()
        if connection is None:
            return False
        try:
            return bool(connection.exists(key))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.exists failed for %s: %s", key, exc, exc_info=True)
            return False

    def incr(self, key: str) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.incr(key))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.incr failed for %s: %s", key, exc, exc_info=True)
            return 0

    def decr(self, key: str) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.decr(key))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.decr failed for %s: %s", key, exc, exc_info=True)
            return 0

    def expire(self, key: str, ttl_seconds: int) -> bool:
        connection = self._connection()
        if connection is None:
            return False
        try:
            return bool(connection.expire(key, ttl_seconds))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.expire failed for %s: %s", key, exc, exc_info=True)
            return False

    def get_many(self, keys: list[str], *, transactional: bool = True) -> list[str | None]:
        connection = self._connection()
        if connection is None:
            return [None for _ in keys]
        try:
            pipe = connection.pipeline(transaction=transactional)
            for key in keys:
                pipe.get(key)
            values = pipe.execute()
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.get_many failed for %s: %s", keys, exc, exc_info=True)
            return [None for _ in keys]
        return [None if value is None else str(value) for value in values]

    def scan_prefix(self, prefix: str, *, count: int = 100) -> list[str]:
        connection = self._connection()
        if connection is None:
            return []
        try:
            return [str(key) for key in connection.scan_iter(f"{prefix}*", count=count)]
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync kv.scan_prefix failed for %s: %s", prefix, exc, exc_info=True)
            return []


class SyncRedisLockAdapter(SyncRedisComponent):
    def acquire(self, name: str, *, ttl: int = 20) -> bool:
        connection = self._connection()
        if connection is None:
            return False
        try:
            return connection.set(f"{KEY_LOCK_PREFIX}{name}", "locked", nx=True, ex=ttl) is True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync locks.acquire failed for %s: %s", name, exc, exc_info=True)
            return False

    def release(self, name: str) -> bool:
        connection = self._connection()
        if connection is None:
            return False
        try:
            connection.delete(f"{KEY_LOCK_PREFIX}{name}")
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync locks.release failed for %s: %s", name, exc, exc_info=True)
            return False


class SyncRedisPubSubAdapter(SyncRedisComponent):
    def publish(self, channel: str, message: str) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.publish(channel, message))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync pubsub.publish failed for %s: %s", channel, exc, exc_info=True)
            return 0

    def session(self) -> Any | None:
        connection = self._connection()
        if connection is None:
            return None
        try:
            return SyncRedisPubSubSession(connection.pubsub(), self.logger)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync pubsub.session failed: %s", exc, exc_info=True)
            return None


class SyncRedisPubSubSession:
    def __init__(self, raw_pubsub: Any, logger: Any) -> None:
        self._raw_pubsub = raw_pubsub
        self._logger = logger

    def subscribe(self, *channels: str) -> None:
        try:
            self._raw_pubsub.subscribe(*channels)
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.error("sync pubsub.subscribe failed for %s: %s", channels, exc, exc_info=True)
            raise

    def unsubscribe(self, *channels: str) -> None:
        try:
            self._raw_pubsub.unsubscribe(*channels)
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.debug("sync pubsub.unsubscribe failed for %s: %s", channels, exc)

    def get_message(
        self,
        *,
        timeout: float = 0.0,
        ignore_subscribe_messages: bool = True,
    ) -> dict[str, Any] | None:
        try:
            message = self._raw_pubsub.get_message(
                timeout=timeout,
                ignore_subscribe_messages=ignore_subscribe_messages,
            )
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.debug("sync pubsub.get_message failed: %s", exc)
            return None
        return message if isinstance(message, dict) else None

    def close(self) -> None:
        closer = getattr(self._raw_pubsub, "close", None)
        if closer is None:
            return
        try:
            closer()
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.debug("sync pubsub.close failed: %s", exc)


class SyncRedisStreamAdapter(SyncRedisComponent):
    def xadd(self, stream: str, fields: dict[str, Any], **kwargs: Any) -> str | None:
        connection = self._connection()
        if connection is None:
            return None
        try:
            return connection.xadd(stream, fields, **kwargs)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync streams.xadd failed for %s: %s", stream, exc, exc_info=True)
            return None

    def xack(self, stream: str, group: str, message_id: str) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.xack(stream, group, message_id))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync streams.xack failed for %s: %s", stream, exc, exc_info=True)
            return 0

    def xreadgroup(
        self,
        *,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
        noack: bool = False,
    ) -> Any:
        connection = self._connection()
        if connection is None:
            return []
        try:
            return connection.xreadgroup(
                groupname=groupname,
                consumername=consumername,
                streams=streams,
                count=count,
                block=block,
                noack=noack,
            )
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync streams.xreadgroup failed for %s: %s", streams, exc, exc_info=True)
            return []

    def xrange(self, stream: str, min_id: str = "-", max_id: str = "+", count: int | None = None) -> Any:
        connection = self._connection()
        if connection is None:
            return []
        try:
            return connection.xrange(stream, min=min_id, max=max_id, count=count)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync streams.xrange failed for %s: %s", stream, exc, exc_info=True)
            return []

    def xpending_range(
        self,
        stream: str,
        group: str,
        min_id: str,
        max_id: str,
        count: int,
        consumer_name: str | None = None,
    ) -> Any:
        connection = self._connection()
        if connection is None:
            return []
        try:
            return connection.xpending_range(stream, group, min_id, max_id, count, consumer_name)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync streams.xpending_range failed for %s: %s", stream, exc, exc_info=True)
            return []

    def xinfo_consumers(self, stream: str, group: str) -> Any:
        connection = self._connection()
        if connection is None:
            return []
        try:
            return connection.xinfo_consumers(stream, group)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync streams.xinfo_consumers failed for %s: %s", stream, exc, exc_info=True)
            return []


class SyncRedisHashAdapter(SyncRedisComponent):
    def get(self, key: str, field: str) -> str | None:
        connection = self._connection()
        if connection is None:
            return None
        try:
            value = connection.hget(key, field)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync hashes.get failed for %s[%s]: %s", key, field, exc, exc_info=True)
            return None
        return None if value is None else str(value)

    def get_all(self, key: str) -> dict[str, str]:
        connection = self._connection()
        if connection is None:
            return {}
        try:
            return {str(name): str(value) for name, value in connection.hgetall(key).items()}
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync hashes.get_all failed for %s: %s", key, exc, exc_info=True)
            return {}

    def set_mapping(self, key: str, mapping: dict[str, Any]) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.hset(key, mapping=mapping))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync hashes.set_mapping failed for %s: %s", key, exc, exc_info=True)
            return 0


class SyncRedisSortedSetAdapter(SyncRedisComponent):
    def add(self, key: str, mapping: dict[str, float]) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.zadd(key, mapping))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync sorted_sets.add failed for %s: %s", key, exc, exc_info=True)
            return 0

    def remove(self, key: str, *members: str) -> int:
        connection = self._connection()
        if connection is None:
            return 0
        try:
            return int(connection.zrem(key, *members))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync sorted_sets.remove failed for %s: %s", key, exc, exc_info=True)
            return 0

    def range_by_score(self, key: str, min_score: float | str, max_score: float | str) -> list[str]:
        connection = self._connection()
        if connection is None:
            return []
        try:
            return [str(item) for item in connection.zrangebyscore(key, min_score, max_score)]
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sync sorted_sets.range_by_score failed for %s: %s", key, exc, exc_info=True)
            return []


class SyncRedisClient:
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
        self.logger = get_logger("sync_redis_client", request_id)
        self._redis: Any | None = None

        self.kv = SyncRedisKVAdapter(self)
        self.locks = SyncRedisLockAdapter(self)
        self.pubsub = SyncRedisPubSubAdapter(self)
        self.streams = SyncRedisStreamAdapter(self)
        self.hashes = SyncRedisHashAdapter(self)
        self.sorted_sets = SyncRedisSortedSetAdapter(self)

    def connect(self) -> None:
        if redis is None:
            raise RuntimeError("redis package not installed (pip install redis)")
        if self._redis is not None:
            return
        self._redis = redis.Redis(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            db=self.db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            socket_keepalive=True,
        )
        self._redis.ping()

    def close(self) -> None:
        if self._redis is None:
            return
        closer = getattr(self._redis, "close", None)
        if closer is not None:
            closer()
        self._redis = None

    def ping(self) -> bool:
        if self._redis is None:
            return False
        try:
            self._redis.ping()
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.debug("Sync Redis ping failed: %s", exc)
            return False
        return True

    def _require_connection(self) -> Any | None:
        return self._redis


__all__ = ("SyncRedisClient",)
