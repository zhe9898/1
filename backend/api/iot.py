"""
ZEN70 IoT API - IoT 设备状态查询与控制端点。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as redis
except ImportError as exc:
    logger.warning("iot_redis_runtime_unavailable: %s", exc)
    redis = None  # type: ignore[assignment]

router = APIRouter(prefix="/api/v1/iot", tags=["iot"])


def ensure_iot_runtime_available() -> None:
    if redis is None:
        raise RuntimeError("IoT Redis runtime is unavailable")
