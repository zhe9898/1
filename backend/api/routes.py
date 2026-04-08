"""
ZEN70 API v1 璺敱锛氳兘鍔涚煩闃点€佽蒋寮€鍏炽€丼SE 浜嬩欢娴併€?
娉曞吀 搂2.1 寮哄埗锛氬墠绔瘡 30s 鍙戦€?Ping锛屽悗绔?45s 鏈敹鍒板繀椤?cancel() 閲婃斁 FD銆?Client-Token-in-URL + Redis SETEX 瀹炵幇璺?Worker 涓€鑷寸殑瓒呮椂鐔旀柇銆?"""

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
from backend.control_plane.auth.access_policy import has_admin_role
from backend.core.errors import zen
from backend.core.redis_client import (
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
    RedisClient,
)
from backend.core.structured_logging import get_logger
from backend.kernel.profiles.public_profile import normalize_gateway_profile

logger = get_logger("api.routes", None)


# 娉曞吀 搂2.1: SSE 瓒呮椂甯搁噺
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
    summary="鑾峰彇鑳藉姏鐭╅樀",
)
async def get_capabilities(
    request: Request,
    current_user: dict | None = Depends(get_current_user_optional),
) -> dict:
    """
    杩斿洖褰撳墠鎵€鏈夋湇鍔¤兘鍔涖€?
    娉曞吀 2.3.1锛氫緵鍓嶇 v-for 鍔ㄦ€佹覆鏌撱€?    娉曞吀 3.2.5锛歊edis 澶辫仈鏃惰繑鍥?All-OFF 鐭╅樀骞跺甫 X-ZEN70-Bus-Status: not-ready銆?
    淇锛氫箣鍓?redis is None 鏃惰繑鍥炵┖ {}锛屽鑷村墠绔?鏆傛棤鑳藉姏鏁版嵁"銆?    鐜板湪璧?capabilities.get_capabilities_matrix()锛孯edis 涓嶅彲鐢ㄦ椂鍥為€€ ALL_OFF_MATRIX銆?    """
    del request
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    is_admin = has_admin_role(current_user)
    matrix = build_public_capability_matrix(runtime_profile, is_admin=is_admin)

    # 搴忓垪鍖?CapabilityItem 鈫?dict
    serialized = {k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v for k, v in matrix.items()}

    return serialized


# -------------------- SSE Ping 绔偣 --------------------


class SSEPingRequest(BaseModel):
    """Heartbeat payload used to keep an SSE connection alive."""

    connection_id: str = Field(..., description="SSE 寤鸿繛鏃剁殑 client_token")


@router.post(
    "/events/ping",
    summary="SSE 蹇冭烦缁湡",
)
async def sse_ping(
    body: SSEPingRequest,
    redis: RedisClient | None = Depends(get_redis),
    current_user: dict = Depends(get_current_user),
) -> dict[str, bool]:
    """
    Frontend clients call this every ~30 seconds to keep the SSE connection
    alive; the server treats the channel as stale after roughly 45 seconds
    without a ping.

    The ping timestamp is stored via Redis SETEX so multiple Uvicorn workers
    share the same liveness view. Authentication is still required here to
    prevent anonymous ping flooding.
    """
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


# -------------------- SSE 浜嬩欢娴?--------------------


async def _process_sse_ping_timeout(redis: RedisClient, ping_key: str, conn_id_inner: str) -> bool:
    """Return True when the SSE ping lease has expired for this connection."""
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
        # 棣栧寘锛氬洖鏄?connection_id
        yield f'event: connected\ndata: {{"connection_id":"{conn_id}"}}\n\n'
        while True:
            if await request.is_disconnected():
                break

            # 45s 瓒呮椂妫€鏌?(Redis EXISTS)
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
        # Clean up the ping lease and pubsub subscription on exit.
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
    summary="SSE Event Stream",
)
async def sse_events(
    request: Request,
    redis: RedisClient | None = Depends(get_redis),
    client_token: str | None = Query(None, description="鍓嶇鐢熸垚鐨勮繛鎺?UUID锛岀敤浜?Ping 鍏宠仈"),
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """Stream control-plane events over SSE for the authenticated session."""
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

    # 纭畾 connection_id锛氫紭鍏堜娇鐢ㄥ墠绔彁渚涚殑 client_token锛屽惁鍒欏悗绔厹搴曠敓鎴?    conn_id: str
    if client_token and _UUID_RE.match(client_token):
        conn_id = client_token
    else:
        conn_id = str(uuid.uuid4())

    # 鍦?Redis 涓敞鍐屽垵濮?Ping 鏃堕棿鎴筹紙SETEX 45s TTL锛?    ping_key = f"{SSE_PING_KEY_PREFIX}{conn_id}"
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
