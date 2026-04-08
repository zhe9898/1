from __future__ import annotations

from typing import Any

from backend.platform.redis._shared import AsyncRedisComponent, REDIS_OPERATION_ERRORS


class RedisPubSubSession:
    def __init__(self, raw_pubsub: Any, logger: Any) -> None:
        self._raw_pubsub = raw_pubsub
        self._logger = logger

    async def subscribe(self, *channels: str) -> None:
        try:
            await self._raw_pubsub.subscribe(*channels)
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.error("pubsub.subscribe failed for %s: %s", channels, exc, exc_info=True)
            raise

    async def unsubscribe(self, *channels: str) -> None:
        try:
            await self._raw_pubsub.unsubscribe(*channels)
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.debug("pubsub.unsubscribe failed for %s: %s", channels, exc)

    async def get_message(
        self,
        *,
        timeout: float = 0.0,
        ignore_subscribe_messages: bool = True,
    ) -> dict[str, Any] | None:
        try:
            message = await self._raw_pubsub.get_message(
                timeout=timeout,
                ignore_subscribe_messages=ignore_subscribe_messages,
            )
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.debug("pubsub.get_message failed: %s", exc)
            return None
        return message if isinstance(message, dict) else None

    async def close(self) -> None:
        closer = getattr(self._raw_pubsub, "aclose", None) or getattr(self._raw_pubsub, "close", None)
        if closer is None:
            return
        try:
            result = closer()
            if hasattr(result, "__await__"):
                await result
        except REDIS_OPERATION_ERRORS as exc:
            self._logger.debug("pubsub.close failed: %s", exc)


class RedisPubSubAdapter(AsyncRedisComponent):
    async def publish(self, channel: str, message: str) -> int:
        connection = await self._connection()
        if connection is None:
            return 0
        try:
            return int(await connection.publish(channel, message))
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("pubsub.publish failed for %s: %s", channel, exc, exc_info=True)
            return 0

    async def session(self) -> RedisPubSubSession | None:
        connection = await self._connection()
        if connection is None:
            return None
        try:
            raw_pubsub = connection.pubsub()
        except REDIS_OPERATION_ERRORS as exc:
            self.logger.error("pubsub.session failed: %s", exc, exc_info=True)
            return None
        return RedisPubSubSession(raw_pubsub, self.logger)


__all__ = ("RedisPubSubAdapter", "RedisPubSubSession")
