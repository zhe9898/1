from __future__ import annotations

from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, AsyncRedisComponent
from backend.platform.redis.constants import KEY_LOCK_PREFIX


class RedisLockAdapter(AsyncRedisComponent):
    async def acquire(self, name: str, *, ttl: int = 20) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = f"{KEY_LOCK_PREFIX}{name}"
        try:
            return await connection.set(key, "locked", nx=True, ex=ttl) is True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("locks.acquire failed for %s: %s", name, exc, exc_info=True)
            return False

    async def release(self, name: str) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = f"{KEY_LOCK_PREFIX}{name}"
        try:
            await connection.delete(key)
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("locks.release failed for %s: %s", name, exc, exc_info=True)
            return False

    async def exists(self, name: str) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = f"{KEY_LOCK_PREFIX}{name}"
        try:
            return bool(await connection.exists(key))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("locks.exists failed for %s: %s", name, exc, exc_info=True)
            return False


__all__ = ("RedisLockAdapter",)
