"""
ZEN70 API v1 路由：能力矩阵、软开关、SSE 事件流。

法典 §2.1 强制：前端每 30s 发送 Ping，后端 45s 未收到必须 cancel() 释放 FD。
Client-Token-in-URL + Redis SETEX 实现跨 Worker 一致的超时熔断。
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.deps import get_current_user, get_current_user_optional, get_redis
from backend.api.models import CapabilityResponse
from backend.capabilities import build_public_capability_matrix
from backend.core.errors import zen
from backend.core.gateway_profile import normalize_gateway_profile
from backend.core.redis_client import (
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
    RedisClient,
)
from backend.core.structured_logging import get_logger

logger = get_logger("api.routes", None)


# 法典 §2.1: SSE 超时常量
SSE_PING_TIMEOUT = 45
SSE_PING_TTL = SSE_PING_TIMEOUT + 5
SSE_PING_KEY_PREFIX = "sse:ping:"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _next_sse_ping_deadline() -> str:
    return str(time.time() + SSE_PING_TIMEOUT)


@router.get(
    "/capabilities",
    response_model=dict[str, CapabilityResponse],
    summary="获取能力矩阵",
)
async def get_capabilities(
    request: Request,
    current_user: dict | None = Depends(get_current_user_optional),
) -> dict:
    """
    返回当前所有服务能力。

    法典 2.3.1：供前端 v-for 动态渲染。
    法典 3.2.5：Redis 失联时返回 All-OFF 矩阵并带 X-ZEN70-Bus-Status: not-ready。

    修复：之前 redis is None 时返回空 {}，导致前端"暂无能力数据"。
    现在走 capabilities.get_capabilities_matrix()，Redis 不可用时回退 ALL_OFF_MATRIX。
    """
    del request
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    is_admin = bool(current_user and current_user.get("role") == "admin")
    matrix = build_public_capability_matrix(runtime_profile, is_admin=is_admin)

    # 序列化 CapabilityItem → dict
    serialized = {k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v for k, v in matrix.items()}

    return serialized


# -------------------- SSE Ping 端点 --------------------


class SSEPingRequest(BaseModel):
    """法典 §2.1: 前端每 30s 调此接口续期 SSE 连接，防 45s 超时斩杀。"""

    connection_id: str = Field(..., description="SSE 建连时的 client_token")


@router.post(
    "/events/ping",
    summary="SSE 心跳续期",
)
async def sse_ping(
    body: SSEPingRequest,
    redis: RedisClient | None = Depends(get_redis),
    current_user: dict = Depends(get_current_user),
) -> dict[str, bool]:
    """
    法典 §2.1: 前端每 30s 调此接口续期 SSE，否则 45s 后服务端 cancel。

    使用 Redis SETEX 存储 Ping 时间戳，确保跨 Uvicorn Worker 一致性。
    鉴权防注入：Depends(get_current_user) 拦截匿名恶意灌水。
    """
    # 格式校验：仅接受 UUID 格式，防止注入
    if not _UUID_RE.match(body.connection_id):
        raise zen(
            "ZEN-SSE-4001",
            "Invalid connection_id format",
            status_code=400,
            recovery_hint="connection_id must be a valid UUID",
        )
    if redis is not None:
        try:
            await redis.setex(
                f"{SSE_PING_KEY_PREFIX}{body.connection_id}",
                SSE_PING_TTL,
                _next_sse_ping_deadline(),
            )
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.warning("SSE ping Redis write failed: %s", exc)
    return {"ok": True}


# -------------------- SSE 事件流 --------------------


async def _process_sse_ping_timeout(redis: RedisClient, ping_key: str, conn_id_inner: str) -> bool:
    """检查 SSE Ping 超时，返回 True 表示应该掐断连接。"""
    try:
        deadline_raw = await redis.get(ping_key)
        if deadline_raw is None:
            logger.info(
                "SSE timeout: connection %s exceeded %ds without ping",
                conn_id_inner,
                SSE_PING_TIMEOUT,
            )
            return True
        try:
            deadline = float(deadline_raw)
        except (TypeError, ValueError):
            logger.info("SSE timeout metadata invalid for connection %s", conn_id_inner)
            return True
        if time.time() >= deadline:
            logger.info(
                "SSE timeout: connection %s exceeded %ds without ping",
                conn_id_inner,
                SSE_PING_TIMEOUT,
            )
            return True
    except (OSError, ConnectionError, ValueError, KeyError, RuntimeError, TypeError, asyncio.TimeoutError):
        logger.debug("SSE ping timeout check failed for connection %s", conn_id_inner)
    return False


async def _process_pubsub_message(message: dict | None) -> str | None:
    if message and message.get("type") == "message":
        channel_raw = message.get("channel", "")
        data_raw = message.get("data", "{}")
        channel = channel_raw.decode("utf-8", errors="replace") if isinstance(channel_raw, bytes) else str(channel_raw)
        data = data_raw.decode("utf-8", errors="replace") if isinstance(data_raw, bytes) else str(data_raw)
        return f"event: {channel}\ndata: {data}\n\n"
    return ": heartbeat\n\n"


async def _sse_event_generator(request: Request, redis: RedisClient, pubsub: Any, conn_id: str, ping_key: str) -> AsyncGenerator[str, None]:
    """SSE event generator for kernel control-plane events only.

    Subscribed channels:
    - CHANNEL_NODE_EVENTS: Node registration, heartbeat, drain
    - CHANNEL_JOB_EVENTS: Job creation, lease, completion, failure
    - CHANNEL_CONNECTOR_EVENTS: Connector registration, invocation
    - CHANNEL_RESERVATION_EVENTS: Reservation lifecycle and backfill planning
    - CHANNEL_TRIGGER_EVENTS: Trigger lifecycle, fire, delivery audit

    Business/IoT channels (hardware, switch) are NOT subscribed in default kernel.
    """
    try:
        await pubsub.subscribe(
            CHANNEL_NODE_EVENTS,
            CHANNEL_JOB_EVENTS,
            CHANNEL_CONNECTOR_EVENTS,
            CHANNEL_RESERVATION_EVENTS,
            CHANNEL_TRIGGER_EVENTS,
        )
        # 首包：回显 connection_id
        yield f'event: connected\ndata: {{"connection_id":"{conn_id}"}}\n\n'
        while True:
            if await request.is_disconnected():
                break

            # 45s 超时检查 (Redis EXISTS)
            if await _process_sse_ping_timeout(redis, ping_key, conn_id):
                break

            try:
                message_dict = await asyncio.wait_for(
                    pubsub.get_message(timeout=1.0, ignore_subscribe_messages=True),
                    timeout=2.0,
                )
                out_msg = await _process_pubsub_message(message_dict)
                if out_msg:
                    yield out_msg
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                break
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
                logger.debug("SSE event loop: %s", e)
                yield ": heartbeat\n\n"
    finally:
        # 清理 Redis 中的 Ping 键
        try:
            await redis.delete(ping_key)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError):
            logger.debug("Failed to delete SSE ping key during cleanup")
        try:
            await pubsub.unsubscribe(
                CHANNEL_NODE_EVENTS,
                CHANNEL_JOB_EVENTS,
                CHANNEL_CONNECTOR_EVENTS,
                CHANNEL_RESERVATION_EVENTS,
                CHANNEL_TRIGGER_EVENTS,
            )
        except (ConnectionError, asyncio.CancelledError):
            logger.debug("PubSub unsubscribe failed during SSE cleanup")
        try:
            await pubsub.aclose()
        except (ConnectionError, asyncio.CancelledError):
            logger.debug("PubSub close failed during SSE cleanup")


@router.get(
    "/events",
    summary="SSE 事件流",
)
async def sse_events(
    request: Request,
    redis: RedisClient | None = Depends(get_redis),
    client_token: str | None = Query(None, description="前端生成的连接 UUID，用于 Ping 关联"),
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """
    订阅硬件状态变更与软开关事件，以 Server-Sent Events 推送。
    """
    if redis is None:
        raise zen(
            "ZEN-SSE-5001",
            "Redis not available",
            status_code=503,
            recovery_hint="Wait for bus ready and retry; do not loop",
        )
    pubsub = redis.pubsub()
    if pubsub is None:
        raise zen(
            "ZEN-SSE-5002",
            "Redis pubsub unavailable",
            status_code=503,
            recovery_hint="Wait for bus ready and retry; do not loop",
        )

    # 确定 connection_id：优先使用前端提供的 client_token，否则后端兜底生成
    conn_id: str
    if client_token and _UUID_RE.match(client_token):
        conn_id = client_token
    else:
        conn_id = str(uuid.uuid4())

    # 在 Redis 中注册初始 Ping 时间戳（SETEX 45s TTL）
    ping_key = f"{SSE_PING_KEY_PREFIX}{conn_id}"
    try:
        await redis.setex(ping_key, SSE_PING_TTL, _next_sse_ping_deadline())
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.warning("SSE initial ping registration failed: %s", exc)

    return StreamingResponse(
        _sse_event_generator(request, redis, pubsub, conn_id, ping_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
