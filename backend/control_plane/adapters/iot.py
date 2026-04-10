"""
ZEN70 IoT API - IoT 设备状态查询与控制端点。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.platform.redis.runtime import redis_sdk_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/iot", tags=["iot"])


def ensure_iot_runtime_available() -> None:
    if not redis_sdk_available():
        raise RuntimeError("IoT Redis runtime is unavailable")
