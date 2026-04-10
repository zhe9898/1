from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.control_plane.adapters.routes import (
    SSE_PING_KEY_PREFIX,
    SSE_PING_TIMEOUT,
    SSE_PING_TTL,
    SSEPingRequest,
    _process_sse_ping_timeout,
    _sse_event_generator,
    sse_events,
    sse_ping,
)
from backend.platform.events.channels import (
    CHANNEL_SWITCH_COMMANDS,
    CONTROL_PLANE_REALTIME_CHANNELS,
    browser_realtime_subscription_subjects,
    tenant_realtime_subject,
)
from backend.platform.events.types import ControlEvent
from backend.platform.redis.client import CHANNEL_JOB_EVENTS


@pytest.mark.asyncio
async def test_sse_ping_stores_explicit_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = SimpleNamespace(kv=SimpleNamespace(setex=AsyncMock()))
    now = 100.0
    monkeypatch.setattr("backend.control_plane.adapters.routes.time.time", lambda: now)
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
    monkeypatch.setattr("backend.control_plane.adapters.routes.time.time", lambda: 100.0)

    should_disconnect = await _process_sse_ping_timeout(redis, "sse:ping:1", "conn-1")

    assert should_disconnect is True


@pytest.mark.asyncio
async def test_process_sse_ping_timeout_keeps_connection_when_deadline_is_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = SimpleNamespace(kv=SimpleNamespace(get=AsyncMock(return_value="120.0")))
    monkeypatch.setattr("backend.control_plane.adapters.routes.time.time", lambda: 100.0)

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

    generator = _sse_event_generator(request, redis, subscription, "conn-1", "sse:ping:1", tenant_id="tenant-a")
    first_frame = await generator.__anext__()
    await generator.aclose()

    assert "event: connected" in first_frame
    subscription.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sse_event_generator_formats_control_event_messages() -> None:
    request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, True]))
    redis = SimpleNamespace(kv=SimpleNamespace(delete=AsyncMock(), get=AsyncMock(return_value="9999999999")))
    subscription = SimpleNamespace(
        get_message=AsyncMock(
            return_value=ControlEvent(
                subject=tenant_realtime_subject(CHANNEL_JOB_EVENTS, "tenant-a"),
                data='{"tenant_id":"tenant-a","job":{"job_id":"job-1"}}',
            )
        ),
        close=AsyncMock(),
    )

    generator = _sse_event_generator(request, redis, subscription, "conn-1", "sse:ping:1", tenant_id="tenant-a")
    connected_frame = await generator.__anext__()
    event_frame = await generator.__anext__()
    await generator.aclose()

    assert "event: connected" in connected_frame
    assert f"event: {CHANNEL_JOB_EVENTS}" in event_frame
    assert '"tenant_id":"tenant-a"' in event_frame


@pytest.mark.asyncio
async def test_sse_event_generator_drops_other_tenant_job_events() -> None:
    request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, False, True]))
    redis = SimpleNamespace(kv=SimpleNamespace(delete=AsyncMock(), get=AsyncMock(return_value="9999999999")))
    subscription = SimpleNamespace(
        get_message=AsyncMock(
            side_effect=[
                ControlEvent(
                    subject=tenant_realtime_subject(CHANNEL_JOB_EVENTS, "tenant-b"),
                    data='{"tenant_id":"tenant-b","job":{"job_id":"job-2"}}',
                ),
                None,
            ]
        ),
        close=AsyncMock(),
    )

    generator = _sse_event_generator(request, redis, subscription, "conn-1", "sse:ping:1", tenant_id="tenant-a")
    connected_frame = await generator.__anext__()
    next_frame = await generator.__anext__()
    await generator.aclose()

    assert "event: connected" in connected_frame
    assert next_frame == ": heartbeat\n\n"


@pytest.mark.asyncio
async def test_sse_event_generator_requires_tenant_id_for_tenant_scoped_events() -> None:
    request = SimpleNamespace(is_disconnected=AsyncMock(side_effect=[False, False, True]))
    redis = SimpleNamespace(kv=SimpleNamespace(delete=AsyncMock(), get=AsyncMock(return_value="9999999999")))
    subscription = SimpleNamespace(
        get_message=AsyncMock(
            side_effect=[
                ControlEvent(
                    subject=tenant_realtime_subject(CHANNEL_JOB_EVENTS, "tenant-a"),
                    data='{"job":{"job_id":"job-3"}}',
                ),
                None,
            ]
        ),
        close=AsyncMock(),
    )

    generator = _sse_event_generator(request, redis, subscription, "conn-1", "sse:ping:1", tenant_id="tenant-a")
    await generator.__anext__()
    next_frame = await generator.__anext__()
    await generator.aclose()

    assert next_frame == ": heartbeat\n\n"


@pytest.mark.asyncio
async def test_sse_events_subscribes_public_and_tenant_scoped_subjects() -> None:
    request = SimpleNamespace()
    redis = SimpleNamespace(kv=SimpleNamespace(setex=AsyncMock()))
    subscription = SimpleNamespace(get_message=AsyncMock(return_value=None), close=AsyncMock())
    event_bus = SimpleNamespace(subscribe=AsyncMock(return_value=subscription))

    response = await sse_events(
        request,
        redis=redis,
        event_bus=event_bus,
        current_user={"sub": "user-1", "tenant_id": "tenant-a"},
    )

    assert response.media_type == "text/event-stream"
    event_bus.subscribe.assert_awaited_once_with(browser_realtime_subscription_subjects("tenant-a"))
    redis.kv.setex.assert_awaited_once()


def test_sse_realtime_whitelist_excludes_internal_redis_signals() -> None:
    assert CHANNEL_SWITCH_COMMANDS not in CONTROL_PLANE_REALTIME_CHANNELS
