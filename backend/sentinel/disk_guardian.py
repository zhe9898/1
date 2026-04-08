"""Disk usage guardian for readonly protection and degradation events."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from typing import Any

from backend.platform.redis import SyncRedisClient
from backend.platform.redis.constants import CHANNEL_SWITCH_EVENTS, KEY_SYSTEM_READONLY_DISK

logger = logging.getLogger("disk-guardian")

DISK_CHECK_INTERVAL_SEC = 60
DISK_CRITICAL_THRESHOLD = 95.0
DISK_WARNING_THRESHOLD = 90.0

REDIS_KEY_DISK_READONLY = KEY_SYSTEM_READONLY_DISK
REDIS_CHANNEL_DISK = CHANNEL_SWITCH_EVENTS


def get_system_disk_usage(path: str = "/") -> tuple[float, float, float]:
    try:
        usage = shutil.disk_usage(path)
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        usage_percent = (usage.used / usage.total) * 100 if usage.total > 0 else 0.0
        return total_gb, used_gb, usage_percent
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("Failed to read disk usage: %s", exc)
        return 0.0, 0.0, 0.0


def check_and_act(
    redis_client: Any | None = None,
    check_path: str = "/",
) -> str:
    total_gb, used_gb, usage_pct = get_system_disk_usage(check_path)
    if usage_pct <= 0:
        logger.warning("Disk usage unavailable, skipping this cycle")
        return "ok"

    if usage_pct >= DISK_CRITICAL_THRESHOLD:
        logger.critical(
            "System disk usage %.1f%% >= %.0f%% (total %.1fGB, used %.1fGB)",
            usage_pct,
            DISK_CRITICAL_THRESHOLD,
            total_gb,
            used_gb,
        )
        if redis_client is not None:
            _publish_disk_event(redis_client, "critical", usage_pct)
            _set_readonly_flag(redis_client, True)
        return "critical"

    if usage_pct >= DISK_WARNING_THRESHOLD:
        logger.warning(
            "System disk usage %.1f%% >= %.0f%% (total %.1fGB, used %.1fGB)",
            usage_pct,
            DISK_WARNING_THRESHOLD,
            total_gb,
            used_gb,
        )
        if redis_client is not None:
            _publish_disk_event(redis_client, "warning", usage_pct)
        return "warning"

    if redis_client is not None:
        _clear_readonly_if_set(redis_client, usage_pct)
    logger.debug("System disk usage %.1f%% is healthy", usage_pct)
    return "ok"


def _publish_disk_event(redis_client: Any, level: str, usage_pct: float) -> None:
    payload = json.dumps(
        {
            "state": "OFF" if level == "critical" else "ON",
            "switch": "disk_guardian",
            "name": "disk_guardian",
            "event": "disk_guardian",
            "level": level,
            "reason": f"disk usage {usage_pct:.1f}% -> {level}",
            "usage_percent": float(round(usage_pct, 1)),
            "action": "readonly_lockdown" if level == "critical" else "warning_alert",
            "updated_at": str(time.time()),
            "updated_by": "disk_guardian",
        }
    )
    try:
        receiver_count = redis_client.pubsub.publish(REDIS_CHANNEL_DISK, payload)
        if receiver_count == 0:
            logger.warning("Published disk:%s event to %s without subscribers", level, REDIS_CHANNEL_DISK)
        else:
            logger.info("Published disk:%s event to %s (%d subscribers)", level, REDIS_CHANNEL_DISK, receiver_count)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("Failed to publish disk event: %s", exc)


def _set_readonly_flag(redis_client: Any, value: bool) -> None:
    try:
        if value:
            redis_client.kv.set(REDIS_KEY_DISK_READONLY, "1")
            logger.warning("Set readonly flag %s=1", REDIS_KEY_DISK_READONLY)
        else:
            redis_client.kv.delete(REDIS_KEY_DISK_READONLY)
            logger.info("Cleared readonly flag %s", REDIS_KEY_DISK_READONLY)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("Failed to update readonly flag: %s", exc)


def _clear_readonly_if_set(redis_client: Any, usage_pct: float) -> None:
    try:
        current = redis_client.kv.get(REDIS_KEY_DISK_READONLY)
        if current:
            logger.info(
                "Disk usage recovered to %.1f%% < %.0f%%; clearing readonly guard",
                usage_pct,
                DISK_CRITICAL_THRESHOLD,
            )
            _set_readonly_flag(redis_client, False)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.error("Failed to check readonly flag: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    redis_host = os.getenv("REDIS_HOST", "redis")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD") or None
    check_path = os.getenv("DISK_CHECK_PATH", "/")

    redis_client: SyncRedisClient | None = None
    try:
        redis_client = SyncRedisClient(
            host=redis_host,
            port=redis_port,
            password=redis_password,
        )
        redis_client.connect()
        logger.info("Connected to Redis: %s:%s", redis_host, redis_port)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.warning("Redis unavailable; disk guardian will run stateless: %s", exc)
        redis_client = None

    logger.info(
        "Disk guardian started: path=%s interval=%ss thresholds=%.0f%%/%.0f%%",
        check_path,
        DISK_CHECK_INTERVAL_SEC,
        DISK_WARNING_THRESHOLD,
        DISK_CRITICAL_THRESHOLD,
    )

    while True:
        try:
            check_and_act(redis_client=redis_client, check_path=check_path)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.error("Disk guardian cycle failed: %s", exc)
        time.sleep(DISK_CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
