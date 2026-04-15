from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.platform.events.channels import is_registered_control_plane_subject
from backend.platform.events.types import ControlEvent, ControlEventSubscription

if TYPE_CHECKING:
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
        if not is_registered_control_plane_subject(subject):
            raise ValueError(f"subject is not a registered control-plane event subject: {subject}")
        await self._redis.pubsub.publish(subject, payload)

    async def subscribe(self, subjects: Sequence[str]) -> RedisEventSubscription:
        subject_tuple = tuple(subjects)
        invalid = [subject for subject in subject_tuple if not is_registered_control_plane_subject(subject)]
        if invalid:
            raise ValueError(f"subjects are not registered control-plane event subjects: {invalid}")
        pubsub = await self._redis.pubsub.session()
        if pubsub is None:
            raise RuntimeError("Redis pubsub unavailable")
        await pubsub.subscribe(*subject_tuple)
        return RedisEventSubscription(pubsub, subject_tuple)

    async def close(self) -> None:
        return None
