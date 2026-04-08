from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, AsyncRedisComponent


class RedisKVAdapter(AsyncRedisComponent):
    async def get(self, key: str) -> str | None:
        connection = await self._connection()
        if connection is None:
            return None
        try:
            return cast("str | None", await connection.get(key))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("kv.get failed for %s: %s", key, exc, exc_info=True)
            return None

    async def set(self, key: str, value: str | bytes | int | float, **kwargs: Any) -> object:
        connection = await self._connection()
        if connection is None:
            return None
        try:
            return await connection.set(key, value, **kwargs)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("kv.set failed for %s: %s", key, exc, exc_info=True)
            return None

    async def setex(self, key: str, ttl_seconds: int, value: str | bytes | int | float) -> object:
        connection = await self._connection()
        if connection is None:
            return None
        try:
            return await connection.setex(key, ttl_seconds, value)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("kv.setex failed for %s: %s", key, exc, exc_info=True)
            return None

    async def set_if_absent(self, key: str, value: str | bytes | int | float, *, ttl_seconds: int) -> bool | None:
        connection = await self._connection()
        if connection is None:
            return None
        try:
            result = await connection.set(key, value, nx=True, ex=ttl_seconds)
            return True if result is True else False
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("kv.set_if_absent failed for %s: %s", key, exc, exc_info=True)
            return None

    async def delete(self, *keys: str) -> int:
        connection = await self._connection()
        if connection is None:
            return 0
        try:
            return int(await connection.delete(*keys))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("kv.delete failed for %s: %s", keys, exc, exc_info=True)
            return 0

    async def incr(self, key: str) -> int:
        connection = await self._connection()
        if connection is None:
            return 0
        try:
            return int(await connection.incr(key))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("kv.incr failed for %s: %s", key, exc, exc_info=True)
            return 0

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        try:
            return bool(await connection.expire(key, ttl_seconds))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("kv.expire failed for %s: %s", key, exc, exc_info=True)
            return False

    async def get_many(self, keys: Sequence[str], *, transactional: bool = True) -> list[str | None]:
        if not keys:
            return []
        connection = await self._connection()
        if connection is None:
            return [None for _ in keys]

        async def _get_many() -> list[str | None]:
            pipe = connection.pipeline(transaction=transactional)
            for key in keys:
                pipe.get(key)
            values = await pipe.execute()
            return [cast("str | None", item) for item in values]

        return await self._retry_once(_get_many, [None for _ in keys], f"kv.get_many({len(keys)})")

    async def scan_prefix(self, prefix: str, *, count: int = 100) -> list[str]:
        connection = await self._connection()
        if connection is None:
            return []

        async def _scan() -> list[str]:
            return [key async for key in connection.scan_iter(f"{prefix}*", count=count)]

        return await self._retry_once(_scan, [], f"kv.scan_prefix({prefix})")


__all__ = ("RedisKVAdapter",)
