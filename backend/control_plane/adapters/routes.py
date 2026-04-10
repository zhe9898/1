"""
ZEN70 API v1 鐠侯垳鏁遍敍姘冲厴閸旀稓鐓╅梼鐐光偓浣借拫瀵偓閸忕偨鈧讣SE 娴滃娆㈠ù浣碘偓?
濞夋洖鍚€ 鎼?.1 瀵搫鍩楅敍姘缁旑垱鐦?30s 閸欐垿鈧?Ping閿涘苯鎮楃粩?45s 閺堫亝鏁归崚鏉跨箑妞?cancel() 闁插﹥鏂?FD閵?Client-Token-in-URL + Redis SETEX 鐎圭偟骞囩捄?Worker 娑撯偓閼峰娈戠搾鍛閻旀梹鏌囬妴?"""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import json
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.capabilities import build_public_capability_matrix
from backend.control_plane.adapters.deps import get_current_user, get_current_user_optional, get_event_bus, get_redis
from backend.control_plane.adapters.models import CapabilityResponse
from backend.control_plane.auth.access_policy import has_admin_role
from backend.control_plane.cache_headers import apply_identity_no_store_headers
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.kernel.profiles.public_profile import normalize_gateway_profile
from backend.platform.events.channels import (
    TENANT_SCOPED_REALTIME_CHANNELS,
    browser_realtime_subscription_subjects,
    control_plane_subject_channel,
    subject_targets_tenant,
)
from backend.platform.events.types import ControlEvent, ControlEventBus, ControlEventSubscription
from backend.platform.logging.structured import get_logger
from backend.platform.redis.client import (
    RedisClient,
)

logger = get_logger("api.routes", None)


# 濞夋洖鍚€ 鎼?.1: SSE 鐡掑懏妞傜敮鎼佸櫤
SSE_PING_TIMEOUT = 45
SSE_PING_TTL = SSE_PING_TIMEOUT + 5
SSE_PING_KEY_PREFIX = "sse:ping:"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

router = APIRouter(prefix="/api/v1", tags=["v1"])
CurrentUserOptionalDependency = Annotated[dict | None, Depends(get_current_user_optional)]
CurrentUserDependency = Annotated[dict, Depends(get_current_user)]
RedisDependency = Annotated[RedisClient | None, Depends(get_redis)]
EventBusDependency = Annotated[ControlEventBus | None, Depends(get_event_bus)]
ClientTokenQuery = Annotated[str | None, Query(description="Optional SSE client token used for ping correlation")]


def _next_sse_ping_deadline() -> str:
    return str(time.time() + SSE_PING_TIMEOUT)


@router.get(
    "/capabilities",
    response_model=dict[str, CapabilityResponse],
    summary="閼惧嘲褰囬懗钘夊閻晠妯€",
)
async def get_capabilities(
    request: Request,
    response: Response,
    current_user: CurrentUserOptionalDependency,
) -> dict:
    """
    鏉╂柨娲栬ぐ鎾冲閹碘偓閺堝婀囬崝陇鍏橀崝娑栤偓?
    濞夋洖鍚€ 2.3.1閿涙矮绶甸崜宥囶伂 v-for 閸斻劍鈧焦瑕嗛弻鎾扁偓?    濞夋洖鍚€ 3.2.5閿涙瓓edis 婢惰精浠堥弮鎯扮箲閸?All-OFF 閻晠妯€楠炶泛鐢?X-ZEN70-Bus-Status: not-ready閵?
    娣囶喖顦查敍姘閸?redis is None 閺冩儼绻戦崶鐐碘敄 {}閿涘苯顕遍懛鏉戝缁?閺嗗倹妫ら懗钘夊閺佺増宓?閵?    閻滄澘婀挧?capabilities.get_capabilities_matrix()閿涘edis 娑撳秴褰查悽銊︽閸ョ偤鈧偓 ALL_OFF_MATRIX閵?"""
    del request
    apply_identity_no_store_headers(response)
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
    redis: RedisDependency,
    current_user: CurrentUserDependency,
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
    except (OSError, ConnectionError, ValueError, KeyError, RuntimeError, TypeError, TimeoutError):
        logger.debug("SSE ping timeout check failed for connection %s", conn_id_inner)
    return False


async def _format_control_event(message: ControlEvent | None) -> str | None:
    if message is not None:
        event_subject = control_plane_subject_channel(message.subject)
        if event_subject is None:
            logger.warning("Dropping SSE event with unregistered control-plane subject: subject=%s", message.subject)
            return None
        return f"event: {event_subject}\ndata: {message.data}\n\n"
    return ": heartbeat\n\n"


def _event_visible_to_tenant(message: ControlEvent, *, tenant_id: str) -> bool:
    event_subject = control_plane_subject_channel(message.subject)
    if event_subject is None:
        logger.warning("Dropping SSE event with unregistered control-plane subject: subject=%s", message.subject)
        return False
    if event_subject not in TENANT_SCOPED_REALTIME_CHANNELS:
        return True
    if not subject_targets_tenant(message.subject, tenant_id):
        logger.warning(
            "Dropping tenant-scoped SSE event delivered on mismatched tenant subject: subject=%s tenant_id=%s",
            message.subject,
            tenant_id,
        )
        return False
    try:
        payload = json.loads(message.data)
    except json.JSONDecodeError:
        logger.warning("Dropping tenant-scoped SSE event with invalid JSON payload: subject=%s", event_subject)
        return False
    if not isinstance(payload, dict):
        logger.warning("Dropping tenant-scoped SSE event with non-object payload: subject=%s", event_subject)
        return False
    event_tenant_id = str(payload.get("tenant_id") or "").strip()
    if not event_tenant_id:
        logger.warning("Dropping tenant-scoped SSE event missing tenant_id: subject=%s", event_subject)
        return False
    return event_tenant_id == tenant_id


async def _sse_event_generator(
    request: Request,
    redis: RedisClient,
    subscription: ControlEventSubscription,
    conn_id: str,
    ping_key: str,
    *,
    tenant_id: str,
) -> AsyncGenerator[str, None]:
    """SSE event generator for browser-visible control-plane events only."""
    try:
        # 妫ｆ牕瀵橀敍姘礀閺?connection_id
        yield f'event: connected\ndata: {{"connection_id":"{conn_id}"}}\n\n'
        while True:
            if await request.is_disconnected():
                break

            # 45s 鐡掑懏妞傚Λ鈧弻?(Redis EXISTS)
            if await _process_sse_ping_timeout(redis, ping_key, conn_id):
                break

            try:
                message = await asyncio.wait_for(subscription.get_message(timeout=1.0), timeout=2.0)
                if message is not None and not _event_visible_to_tenant(message, tenant_id=tenant_id):
                    continue
                out_msg = await _format_control_event(message)
                if out_msg:
                    yield out_msg
            except TimeoutError:
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
            await subscription.close()
        except (ConnectionError, asyncio.CancelledError):
            logger.debug("Event bus subscription close failed during SSE cleanup")


@router.get(
    "/events",
    summary="SSE Event Stream",
)
async def sse_events(
    request: Request,
    redis: RedisDependency,
    event_bus: EventBusDependency,
    current_user: CurrentUserDependency,
    client_token: ClientTokenQuery = None,
) -> StreamingResponse:
    """Stream control-plane events over SSE for the authenticated session."""
    if redis is None:
        raise zen(
            "ZEN-SSE-5001",
            "Redis not available",
            status_code=503,
            recovery_hint="Wait for bus ready and retry; do not loop",
        )
    if event_bus is None:
        raise zen(
            "ZEN-SSE-5002",
            "Event bus unavailable",
            status_code=503,
            recovery_hint="Wait for bus ready and retry; do not loop",
        )
    tenant_id = require_current_user_tenant_id(current_user)
    subscription = await event_bus.subscribe(browser_realtime_subscription_subjects(tenant_id))

    # Reuse a validated client token when available so reconnects keep the same
    # ping lease; otherwise mint a fresh connection id.
    conn_id = client_token if client_token and _UUID_RE.match(client_token) else str(uuid.uuid4())

    # Register the ping lease before streaming so timeout checks have a stable
    # source of truth even if the client disconnects during setup.
    ping_key = f"{SSE_PING_KEY_PREFIX}{conn_id}"
    try:
        await redis.kv.setex(ping_key, SSE_PING_TTL, _next_sse_ping_deadline())
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.warning("SSE initial ping registration failed: %s", exc)

    return StreamingResponse(
        _sse_event_generator(request, redis, subscription, conn_id, ping_key, tenant_id=tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
