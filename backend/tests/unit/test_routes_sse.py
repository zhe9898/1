from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.api.routes import (
    SSE_PING_KEY_PREFIX,
    SSE_PING_TIMEOUT,
    SSE_PING_TTL,
    SSEPingRequest,
    _process_sse_ping_timeout,
    _sse_event_generator,
    sse_ping,
)
from backend.platform.events.channels import CHANNEL_SWITCH_COMMANDS, CONTROL_PLANE_REALTIME_CHANNELS
from backend.platform.events.types import ControlEvent
from backend.platform.redis.client import CHANNEL_JOB_EVENTS


@pytest.mark.asyncio
async def test_sse_ping_stores_explicit_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = SimpleNamespace(kv=SimpleNamespace(setex=AsyncMock()))
    now = 100.0
    monkeypatch.setattr("backend.api.routes.time.time", lambda: now)
    connection_id = str(uuid.uuid4())

    response = await sse_ping(
        SSEPingRequest(connection_id=connection_id),
        redis=redis,
        current_user={"sub": "user-1"},
    )

    assert response == {"ok": True}
    redis.kv.setex.assert_awaited_once()
    key, ttl, deadline = redis.kv.setex.await_args.args
    assert key == f"{SSE_PING_KEY_PREFIX}{connection_id}"
    assert ttl == SSE_PING_TTL
    assert float(deadline) == pytest.approx(now + SSE_PING_TIMEOUT)


@pytest.mark.asyncio
async def test_process_sse_ping_timeout_disconnects_when_deadline_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = SimpleNamespace(kv=SimpleNamespace(get=AsyncMock(return_value="99.0")))
    monkeypatch.setattr("backend.api.routes.time.time", lambda: 100.0)

    should_disconnect = await _process_sse_ping_timeout(redis, "sse:ping:1", "conn-1")

    assert should_disconnect is True


@pytest.mark.asyncio
async def test_process_sse_ping_timeout_keeps_connection_when_deadline_is_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = SimpleNamespace(kv=SimpleNamespace(get=AsyncMock(return_value="120.0")))
    monkeypatch.setattr("backend.api.routes.time.time", lambda: 100.0)

    should_disconnect = await _process_sse_ping_timeout(redis, "sse:ping:1", "conn-1")

    assert should_disconnect is False


@pytest.mark.asyncio
async def test_sse_event_generator_subscribes_hardware_and_switch_channels() -> None:
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    redis = SimpleNamespace(kv=SimpleNamespace(delete=AsyncMock()))
    subscription = SimpleNamespace(
        get_message=AsyncMock(return_value=None),
        close=AsyncMock(),
    )

    generator = _sse_event_generator(request, redis, subscription, "conn-1", "sse:ping:1")
    first_frame = await generator.__anext__()
    await generator.aclose()

    assert "event: connected" in first_frame
    subscription.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sse_event_generator_formats_control_event_messages() -> None:
    request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, True]))
    redis = SimpleNamespace(kv=SimpleNamespace(delete=AsyncMock(), get=AsyncMock(return_value="9999999999")))
    subscription = SimpleNamespace(
        get_message=AsyncMock(return_value=ControlEvent(subject=CHANNEL_JOB_EVENTS, data='{"job_id":"job-1"}')),
        close=AsyncMock(),
    )

    generator = _sse_event_generator(request, redis, subscription, "conn-1", "sse:ping:1")
    connected_frame = await generator.__anext__()
    event_frame = await generator.__anext__()
    await generator.aclose()

    assert "event: connected" in connected_frame
    assert f"event: {CHANNEL_JOB_EVENTS}" in event_frame
    assert '{"job_id":"job-1"}' in event_frame


def test_sse_realtime_whitelist_excludes_internal_redis_signals() -> None:
    assert CHANNEL_SWITCH_COMMANDS not in CONTROL_PLANE_REALTIME_CHANNELS
