from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.platform.events.types import ControlEvent, ControlEventSubscription
from backend.platform.redis.client import RedisClient


@dataclass(slots=True)
class RedisEventSubscription(ControlEventSubscription):
    _pubsub: Any
    _subjects: tuple[str, ...]

    async def get_message(self, timeout: float | None = None) -> ControlEvent | None:
        message = await self._pubsub.get_message(timeout=timeout or 1.0, ignore_subscribe_messages=True)
        if not message or message.get("type") != "message":
            return None
        raw_subject = message.get("channel", "")
        raw_data = message.get("data", "")
        subject = raw_subject.decode("utf-8", errors="replace") if isinstance(raw_subject, bytes) else str(raw_subject)
        data = raw_data.decode("utf-8", errors="replace") if isinstance(raw_data, bytes) else str(raw_data)
        return ControlEvent(subject=subject, data=data)

    async def close(self) -> None:
        try:
            await self._pubsub.unsubscribe(*self._subjects)
        finally:
            await self._pubsub.close()


class RedisEventBus:
    backend_name = "redis"

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def publish(self, subject: str, payload: str) -> None:
        await self._redis.pubsub.publish(subject, payload)

    async def subscribe(self, subjects: Sequence[str]) -> RedisEventSubscription:
        pubsub = await self._redis.pubsub.session()
        if pubsub is None:
            raise RuntimeError("Redis pubsub unavailable")
        subject_tuple = tuple(subjects)
        await pubsub.subscribe(*subject_tuple)
        return RedisEventSubscription(pubsub, subject_tuple)

    async def close(self) -> None:
        return None
