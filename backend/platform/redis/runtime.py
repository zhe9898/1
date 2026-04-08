from __future__ import annotations

import asyncio
import importlib.util
import logging
from collections.abc import Mapping

from backend.platform.redis.client import RedisClient


def redis_sdk_available() -> bool:
    return importlib.util.find_spec("redis.asyncio") is not None


def _int_setting(settings: Mapping[str, object], key: str, default: int) -> int:
    value = settings.get(key, default)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return default


def _optional_str_setting(settings: Mapping[str, object], key: str) -> str | None:
    value = settings.get(key)
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


async def connect_redis_with_retry(
    settings: Mapping[str, object],
    *,
    logger: logging.Logger | logging.LoggerAdapter,
    max_attempts: int = 5,
) -> RedisClient | None:
    """Create a shared Redis client with bounded retry/backoff."""
    backoff = [1, 2, 4, 8, 16]
    for attempt in range(max_attempts):
        client = RedisClient(
            host=str(settings.get("redis_host", "")),
            port=_int_setting(settings, "redis_port", 6379),
            password=_optional_str_setting(settings, "redis_password"),
            db=_int_setting(settings, "redis_db", 0),
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=10.0)
            return client
        except asyncio.TimeoutError:
            logger.warning("Redis connect timeout, attempt %s/%s", attempt + 1, max_attempts)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.warning("Redis connect attempt %s/%s failed: %s", attempt + 1, max_attempts, exc)
        if attempt < max_attempts - 1:
            await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
    return None
