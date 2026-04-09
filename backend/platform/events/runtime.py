from __future__ import annotations

import asyncio
import importlib.util
import logging
from collections.abc import Mapping

from backend.platform.events.nats_bus import NATSEventBus
from backend.platform.events.redis_bus import RedisEventBus
from backend.platform.events.types import ControlEventBus
from backend.platform.redis.client import RedisClient

_RUNTIME_EVENT_BUS: ControlEventBus | None = None


def nats_sdk_available() -> bool:
    return importlib.util.find_spec("nats") is not None


def get_runtime_event_bus() -> ControlEventBus | None:
    return _RUNTIME_EVENT_BUS


def set_runtime_event_bus(event_bus: ControlEventBus | None) -> None:
    global _RUNTIME_EVENT_BUS
    _RUNTIME_EVENT_BUS = event_bus


def resolve_event_bus_backend(settings: Mapping[str, object]) -> str:
    raw = str(settings.get("event_bus_backend") or "").strip().lower()
    if raw:
        return raw
    return "nats" if str(settings.get("nats_url") or "").strip() else "redis"


async def connect_event_bus_with_retry(
    settings: Mapping[str, object],
    *,
    redis: RedisClient | None,
    logger: logging.Logger | logging.LoggerAdapter,
    max_attempts: int = 5,
) -> ControlEventBus | None:
    backend = resolve_event_bus_backend(settings)
    if backend == "redis":
        if redis is None:
            logger.warning("event bus backend is redis but Redis is unavailable")
            return None
        return RedisEventBus(redis)
    if backend != "nats":
        logger.warning("unsupported event bus backend '%s'", backend)
        return None
    if not nats_sdk_available():
        logger.error("event bus backend is nats but nats-py is not installed")
        return None

    nats_url = str(settings.get("nats_url") or "").strip()
    if not nats_url:
        logger.error("event bus backend is nats but NATS_URL is empty")
        return None

    connect_timeout = float(str(settings.get("nats_connect_timeout", 5.0)))
    backoff = [1, 2, 4, 8, 16]
    for attempt in range(max_attempts):
        try:
            return await NATSEventBus.connect(
                nats_url,
                name="zen70-control-plane",
                connect_timeout=connect_timeout,
            )
        except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            logger.warning("NATS connect attempt %s/%s failed: %s", attempt + 1, max_attempts, exc)
        if attempt < max_attempts - 1:
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
    return None
