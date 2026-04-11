from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from backend.platform.events.channels import is_redis_internal_signal
from backend.platform.events.types import ControlEvent

if TYPE_CHECKING:
    from backend.platform.redis.client import RedisClient
    from backend.platform.redis.sync_client import SyncRedisClient


class SyncSignalSubscription(Protocol):
    def get_message(self, timeout: float | None = None) -> ControlEvent | None: ...

    def close(self) -> None: ...


def _validate_internal_signal_subjects(subjects: Sequence[str]) -> tuple[str, ...]:
    subject_tuple = tuple(str(subject).strip() for subject in subjects if str(subject).strip())
    if not subject_tuple:
        raise ValueError("internal signal subscription requires at least one subject")
    invalid = [subject for subject in subject_tuple if not is_redis_internal_signal(subject)]
    if invalid:
        raise ValueError(f"subjects are not registered Redis internal coordination channels: {invalid}")
    return subject_tuple


@dataclass(slots=True)
class AsyncRedisSignalSubscription:
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


@dataclass(slots=True)
class SyncRedisSignalSubscription:
    _pubsub: Any
    _subjects: tuple[str, ...]

    def get_message(self, timeout: float | None = None) -> ControlEvent | None:
        message = self._pubsub.get_message(timeout=timeout or 1.0, ignore_subscribe_messages=True)
        if not message or message.get("type") != "message":
            return None
        raw_subject = message.get("channel", "")
        raw_data = message.get("data", "")
        subject = raw_subject.decode("utf-8", errors="replace") if isinstance(raw_subject, bytes) else str(raw_subject)
        data = raw_data.decode("utf-8", errors="replace") if isinstance(raw_data, bytes) else str(raw_data)
        return ControlEvent(subject=subject, data=data)

    def close(self) -> None:
        try:
            self._pubsub.unsubscribe(*self._subjects)
        finally:
            self._pubsub.close()


class AsyncInternalSignalSubscriber:
    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def subscribe(self, subjects: Sequence[str]) -> AsyncRedisSignalSubscription:
        subject_tuple = _validate_internal_signal_subjects(subjects)
        pubsub = await self._redis.pubsub.session()
        if pubsub is None:
            raise RuntimeError("Redis pubsub unavailable")
        await pubsub.subscribe(*subject_tuple)
        return AsyncRedisSignalSubscription(pubsub, subject_tuple)


class SyncInternalSignalSubscriber:
    def __init__(self, redis: SyncRedisClient) -> None:
        self._redis = redis

    def subscribe(self, subjects: Sequence[str]) -> SyncRedisSignalSubscription:
        subject_tuple = _validate_internal_signal_subjects(subjects)
        pubsub = self._redis.pubsub.session()
        if pubsub is None:
            raise RuntimeError("Redis pubsub unavailable")
        pubsub.subscribe(*subject_tuple)
        return SyncRedisSignalSubscription(pubsub, subject_tuple)


__all__ = (
    "AsyncInternalSignalSubscriber",
    "AsyncRedisSignalSubscription",
    "SyncInternalSignalSubscriber",
    "SyncRedisSignalSubscription",
    "SyncSignalSubscription",
)
