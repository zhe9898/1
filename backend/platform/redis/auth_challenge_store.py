from __future__ import annotations

from typing import cast

from backend.platform.redis._shared import REDIS_OPERATION_ERRORS, AsyncRedisComponent
from backend.platform.redis.constants import KEY_AUTH_CHALLENGE_PREFIX


class RedisAuthChallengeStore(AsyncRedisComponent):
    async def store(self, challenge_id: str, payload: str, *, ttl_seconds: int = 300) -> bool:
        connection = await self._connection()
        if connection is None:
            return False
        key = f"{KEY_AUTH_CHALLENGE_PREFIX}{challenge_id}"
        try:
            await connection.setex(key, ttl_seconds, payload)
            return True
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("auth_challenges.store failed for %s: %s", challenge_id, exc, exc_info=True)
            return False

    async def consume(self, challenge_id: str) -> str | None:
        connection = await self._connection()
        if connection is None:
            return None
        key = f"{KEY_AUTH_CHALLENGE_PREFIX}{challenge_id}"
        try:
            pipe = connection.pipeline(transaction=True)
            pipe.get(key)
            pipe.delete(key)
            results = await pipe.execute()
            return cast("str | None", results[0] if results else None)
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("auth_challenges.consume failed for %s: %s", challenge_id, exc, exc_info=True)
            return None


__all__ = ("RedisAuthChallengeStore",)
