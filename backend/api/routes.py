"""
ZEN70 API v1 鐠侯垳鏁遍敍姘冲厴閸旀稓鐓╅梼鐐光偓浣借拫瀵偓閸忕偨鈧讣SE 娴滃娆㈠ù浣碘偓?
濞夋洖鍚€ 鎼?.1 瀵搫鍩楅敍姘缁旑垱鐦?30s 閸欐垿鈧?Ping閿涘苯鎮楃粩?45s 閺堫亝鏁归崚鏉跨箑妞?cancel() 闁插﹥鏂?FD閵?Client-Token-in-URL + Redis SETEX 鐎圭偟骞囩捄?Worker 娑撯偓閼峰娈戠搾鍛閻旀梹鏌囬妴?"""

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
from backend.kernel.contracts.errors import zen
from backend.platform.redis.client import (
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_RESERVATION_EVENTS,
    CHANNEL_TRIGGER_EVENTS,
    RedisClient,
)
from backend.platform.logging.structured import get_logger
from backend.kernel.profiles.public_profile import normalize_gateway_profile

logger = get_logger("api.routes", None)


# 濞夋洖鍚€ 鎼?.1: SSE 鐡掑懏妞傜敮鎼佸櫤
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
    summary="閼惧嘲褰囬懗钘夊閻晠妯€",
)
async def get_capabilities(
    request: Request,
    current_user: dict | None = Depends(get_current_user_optional),
) -> dict:
    """
    鏉╂柨娲栬ぐ鎾冲閹碘偓閺堝婀囬崝陇鍏橀崝娑栤偓?
    濞夋洖鍚€ 2.3.1閿涙矮绶甸崜宥囶伂 v-for 閸斻劍鈧焦瑕嗛弻鎾扁偓?    濞夋洖鍚€ 3.2.5閿涙瓓edis 婢惰精浠堥弮鎯扮箲閸?All-OFF 閻晠妯€楠炶泛鐢?X-ZEN70-Bus-Status: not-ready閵?
    娣囶喖顦查敍姘閸?redis is None 閺冩儼绻戦崶鐐碘敄 {}閿涘苯顕遍懛鏉戝缁?閺嗗倹妫ら懗钘夊閺佺増宓?閵?    閻滄澘婀挧?capabilities.get_capabilities_matrix()閿涘edis 娑撳秴褰查悽銊︽閸ョ偤鈧偓 ALL_OFF_MATRIX閵?    """
    del request
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    is_admin = has_admin_role(current_user)
    matrix = build_public_capability_matrix(runtime_profile, is_admin=is_admin)

    # 鎼村繐鍨崠?CapabilityItem 閳?dict
    serialized = {k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v for k, v in matrix.items()}

    return serialized


# -------------------- SSE Ping 缁旑垳鍋?--------------------


class SSEPingRequest(BaseModel):
    """Heartbeat payload used to keep an SSE connection alive."""

    connection_id: str = Field(..., description="SSE 瀵ら缚绻涢弮鍓佹畱 client_token")


@router.post(
    "/events/ping",
    summary="SSE 韫囧啳鐑︾紒顓熸埂",
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
            await redis.kv.setex(
                f"{SSE_PING_KEY_PREFIX}{body.connection_id}",
                SSE_PING_TTL,
                _next_sse_ping_deadline(),
            )
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.warning("SSE ping Redis write failed: %s", exc)
    return {"ok": True}


# -------------------- SSE 娴滃娆㈠ù?--------------------


async def _process_sse_ping_timeout(redis: RedisClient, ping_key: str, conn_id_inner: str) -> bool:
    """Return True when the SSE ping lease has expired for this connection."""
    try:
        deadline_raw = await redis.kv.get(ping_key)
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
        # 妫ｆ牕瀵橀敍姘礀閺?connection_id
        yield f'event: connected\ndata: {{"connection_id":"{conn_id}"}}\n\n'
        while True:
            if await request.is_disconnected():
                break

            # 45s 鐡掑懏妞傚Λ鈧弻?(Redis EXISTS)
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
            await redis.kv.delete(ping_key)
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
            await pubsub.close()
        except (ConnectionError, asyncio.CancelledError):
            logger.debug("PubSub close failed during SSE cleanup")


@router.get(
    "/events",
    summary="SSE Event Stream",
)
async def sse_events(
    request: Request,
    redis: RedisClient | None = Depends(get_redis),
    client_token: str | None = Query(None, description="Optional SSE client token used for ping correlation"),
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
    pubsub = await redis.pubsub.session()
    if pubsub is None:
        raise zen(
            "ZEN-SSE-5002",
            "Redis pubsub unavailable",
            status_code=503,
            recovery_hint="Wait for bus ready and retry; do not loop",
        )

    # 绾喖鐣?connection_id閿涙矮绱崗鍫滃▏閻劌澧犵粩顖涘絹娓氭稓娈?client_token閿涘苯鎯侀崚娆忔倵缁旑垰鍘规惔鏇犳晸閹?    conn_id: str
    if client_token and _UUID_RE.match(client_token):
        conn_id = client_token
    else:
        conn_id = str(uuid.uuid4())

    # 閸?Redis 娑擃厽鏁為崘灞藉灥婵?Ping 閺冨爼妫块幋绛圭礄SETEX 45s TTL閿?    ping_key = f"{SSE_PING_KEY_PREFIX}{conn_id}"
    try:
        await redis.kv.setex(ping_key, SSE_PING_TTL, _next_sse_ping_deadline())
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
