from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from backend.platform.events.channels import is_redis_internal_signal, is_registered_control_plane_subject
from backend.platform.events.runtime import connect_event_bus_with_retry, get_runtime_event_bus, resolve_event_bus_backend
from backend.platform.events.types import ControlEventBus
from backend.platform.redis.sync_client import SyncRedisClient

if TYPE_CHECKING:
    from backend.platform.redis.client import RedisClient

LoggerLike = logging.Logger | logging.LoggerAdapter


def event_bus_settings_from_env() -> dict[str, object]:
    return {
        "event_bus_backend": os.getenv("EVENT_BUS_BACKEND", ""),
        "nats_url": os.getenv("NATS_URL", ""),
        "nats_connect_timeout": float(os.getenv("NATS_CONNECT_TIMEOUT", "5.0")),
    }


def _is_registered_control_subject(subject: str) -> bool:
    return is_registered_control_plane_subject(subject)


def _is_registered_internal_subject(subject: str) -> bool:
    return is_redis_internal_signal(subject)


class AsyncEventPublisher:
    def __init__(
        self,
        *,
        settings: Mapping[str, object] | None = None,
        redis: RedisClient | None = None,
        logger: LoggerLike | None = None,
        event_bus: ControlEventBus | None = None,
    ) -> None:
        self._settings = dict(settings or event_bus_settings_from_env())
        self._redis = redis
        self._logger: LoggerLike = logger or logging.getLogger(__name__)
        self._event_bus = event_bus
        self._owns_event_bus = event_bus is not None
        self._connect_lock = asyncio.Lock()

    async def _resolve_event_bus(self) -> ControlEventBus | None:
        runtime_bus = get_runtime_event_bus()
        if runtime_bus is not None:
            return runtime_bus
        if self._event_bus is not None:
            return self._event_bus
        async with self._connect_lock:
            runtime_bus = get_runtime_event_bus()
            if runtime_bus is not None:
                return runtime_bus
            if self._event_bus is None:
                self._event_bus = await connect_event_bus_with_retry(
                    self._settings,
                    redis=self._redis,
                    logger=self._logger,
                    max_attempts=1,
                )
                self._owns_event_bus = self._event_bus is not None
            return self._event_bus

    async def publish_control(self, subject: str, payload: str) -> bool:
        if not _is_registered_control_subject(subject):
            self._logger.warning("control event rejected because subject is not a registered control-plane channel (subject=%s)", subject)
            return False
        event_bus = await self._resolve_event_bus()
        if event_bus is None:
            self._logger.warning("control event dropped because event bus is unavailable (subject=%s)", subject)
            return False
        try:
            await event_bus.publish(subject, payload)
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError, TimeoutError) as exc:
            self._logger.warning("control event publish failed subject=%s err=%s", subject, exc)
            return False

    async def publish_signal(self, subject: str, payload: str) -> int:
        if not _is_registered_internal_subject(subject):
            self._logger.warning("redis internal signal rejected because subject is not a registered coordination channel (subject=%s)", subject)
            return 0
        if self._redis is None:
            self._logger.warning("redis internal signal dropped because Redis is unavailable (subject=%s)", subject)
            return 0
        try:
            return int(await self._redis.pubsub.publish(subject, payload))
        except (OSError, ValueError, KeyError, RuntimeError, TypeError, TimeoutError) as exc:
            self._logger.warning("redis internal signal publish failed subject=%s err=%s", subject, exc)
            return 0

    async def close(self) -> None:
        runtime_bus = get_runtime_event_bus()
        if not self._owns_event_bus or self._event_bus is None or self._event_bus is runtime_bus:
            return
        try:
            await self._event_bus.close()
        finally:
            self._event_bus = None
            self._owns_event_bus = False


class SyncEventPublisher:
    def __init__(
        self,
        *,
        settings: Mapping[str, object] | None = None,
        redis: SyncRedisClient | None = None,
        logger: LoggerLike | None = None,
    ) -> None:
        self._settings = dict(settings or event_bus_settings_from_env())
        self._redis = redis
        self._logger: LoggerLike = logger or logging.getLogger(__name__)
        self._runner: asyncio.Runner | None = None
        self._event_bus: ControlEventBus | None = None

    def _backend(self) -> str:
        return resolve_event_bus_backend(self._settings)

    def _ensure_runner(self) -> asyncio.Runner:
        if self._runner is None:
            self._runner = asyncio.Runner()
        return self._runner

    def _resolve_event_bus(self) -> ControlEventBus | None:
        if self._backend() == "redis":
            return None
        if self._event_bus is not None:
            return self._event_bus
        runner = self._ensure_runner()
        self._event_bus = runner.run(
            connect_event_bus_with_retry(
                self._settings,
                redis=None,
                logger=self._logger,
                max_attempts=1,
            )
        )
        return self._event_bus

    def publish_control(self, subject: str, payload: str) -> bool:
        if not _is_registered_control_subject(subject):
            self._logger.warning("control event rejected because subject is not a registered control-plane channel (subject=%s)", subject)
            return False
        backend = self._backend()
        if backend == "redis":
            if self._redis is None:
                self._logger.warning("control event dropped because Redis is unavailable (subject=%s)", subject)
                return False
            try:
                self._redis.pubsub.publish(subject, payload)
                return True
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                self._logger.warning("control event publish failed subject=%s err=%s", subject, exc)
                return False

        event_bus = self._resolve_event_bus()
        if event_bus is None:
            self._logger.warning("control event dropped because event bus is unavailable (subject=%s)", subject)
            return False
        try:
            self._ensure_runner().run(event_bus.publish(subject, payload))
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError, TimeoutError) as exc:
            self._logger.warning("control event publish failed subject=%s err=%s", subject, exc)
            return False

    def publish_signal(self, subject: str, payload: str) -> int:
        if not _is_registered_internal_subject(subject):
            self._logger.warning("redis internal signal rejected because subject is not a registered coordination channel (subject=%s)", subject)
            return 0
        if self._redis is None:
            self._logger.warning("redis internal signal dropped because Redis is unavailable (subject=%s)", subject)
            return 0
        try:
            return int(self._redis.pubsub.publish(subject, payload))
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            self._logger.warning("redis internal signal publish failed subject=%s err=%s", subject, exc)
            return 0

    def close(self) -> None:
        if self._event_bus is not None and self._runner is not None:
            try:
                self._runner.run(self._event_bus.close())
            finally:
                self._event_bus = None
        if self._runner is not None:
            self._runner.close()
            self._runner = None


__all__ = (
    "AsyncEventPublisher",
    "SyncEventPublisher",
    "event_bus_settings_from_env",
)
