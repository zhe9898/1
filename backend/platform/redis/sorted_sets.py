from __future__ import annotations

from collections.abc import Mapping

from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, AsyncRedisComponent


class RedisSortedSetAdapter(AsyncRedisComponent):
    async def add(self, key: str, mapping: Mapping[str, float]) -> int:
        connection = await self._connection()
        if connection is None:
            return 0
        try:
            return int(await connection.zadd(key, mapping))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sorted_sets.add failed for %s: %s", key, exc, exc_info=True)
            return 0

    async def remove(self, key: str, *members: str) -> int:
        connection = await self._connection()
        if connection is None:
            return 0
        try:
            return int(await connection.zrem(key, *members))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sorted_sets.remove failed for %s: %s", key, exc, exc_info=True)
            return 0

    async def cardinality(self, key: str) -> int:
        connection = await self._connection()
        if connection is None:
            return 0
        try:
            return int(await connection.zcard(key))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sorted_sets.cardinality failed for %s: %s", key, exc, exc_info=True)
            return 0

    async def range_desc(self, key: str, start: int, end: int) -> list[str]:
        connection = await self._connection()
        if connection is None:
            return []
        try:
            return list(await connection.zrevrange(key, start, end))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("sorted_sets.range_desc failed for %s: %s", key, exc, exc_info=True)
            return []


__all__ = ("RedisSortedSetAdapter",)
