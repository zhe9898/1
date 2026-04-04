"""
ZEN70 IoT API - IoT 设备状态查询与控制端点。
"""

from __future__ import annotations

from fastapi import APIRouter

try:
    import redis.asyncio as redis
except ImportError:
    redis = None  # type: ignore[assignment]

router = APIRouter(prefix="/api/v1/iot", tags=["iot"])
