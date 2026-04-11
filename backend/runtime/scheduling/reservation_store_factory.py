from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from backend.platform.redis import SyncRedisClient
from backend.runtime.scheduling.reservation_store import RedisReservationStore, ReservationStore


def read_reservation_store_settings() -> tuple[str, str]:
    store_type = str(os.getenv("ZEN70_RESERVATION_STORE", "memory")).strip().lower() or "memory"
    redis_url = str(os.getenv("ZEN70_RESERVATION_STORE_REDIS_URL", "redis://localhost:6379/0")).strip()
    return store_type, redis_url


def build_reservation_store_from_env(
    *,
    max_reservations: int,
    logger: logging.Logger | None = None,
) -> ReservationStore | None:
    store_type, redis_url = read_reservation_store_settings()
    if store_type == "memory":
        return None
    if store_type != "redis":
        raise RuntimeError(f"ZEN-BACKFILL-STORE-INVALID: unsupported reservation store '{store_type}'")

    parsed = urlparse(redis_url)
    redis_client = SyncRedisClient(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        password=parsed.password,
        db=int((parsed.path or "/0").lstrip("/") or "0"),
        username=parsed.username,
    )
    try:
        redis_client.connect()
    except Exception as exc:
        raise RuntimeError(
            f"ZEN-BACKFILL-STORE-UNAVAILABLE: reservation_store=redis but Redis initialization failed for {redis_url}"
        ) from exc
    if logger is not None:
        logger.info("reservation_store=redis url=%s", redis_url)
    return RedisReservationStore(redis_client, max_reservations)


__all__ = ("build_reservation_store_from_env", "read_reservation_store_settings")
