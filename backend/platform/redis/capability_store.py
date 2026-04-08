from __future__ import annotations

import json

from backend.platform.redis._shared import AsyncRedisComponent, REDIS_OPERATION_ERRORS
from backend.platform.redis.constants import KEY_CAPABILITIES
from backend.platform.redis.types import Capability


class RedisCapabilityStore(AsyncRedisComponent):
    async def get_all(self) -> dict[str, Capability]:
        connection = await self._connection()
        if connection is None:
            return {}

        async def _get_all() -> dict[str, Capability]:
            data = await connection.hgetall(KEY_CAPABILITIES)
            if not data:
                return {}
            result: dict[str, Capability] = {}
            for key, value in data.items():
                try:
                    result[key] = json.loads(value)
                except json.JSONDecodeError:
                    self.logger.warning("Invalid capability JSON for %s: %s", key, value)
            return result

        return await self._retry_once(_get_all, {}, "capabilities.get_all")

    async def set(self, name: str, capability: Capability) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        try:
            await connection.hset(KEY_CAPABILITIES, name, json.dumps(capability))
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("capabilities.set failed for %s: %s", name, exc, exc_info=True)
            return False

    async def delete(self, name: str) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        try:
            await connection.hdel(KEY_CAPABILITIES, name)
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("capabilities.delete failed for %s: %s", name, exc, exc_info=True)
            return False


__all__ = ("RedisCapabilityStore",)
