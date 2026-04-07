from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from backend.api.routes import (
    SSE_PING_KEY_PREFIX,
    SSE_PING_TIMEOUT,
    SSE_PING_TTL,
    SSEPingRequest,
    _process_sse_ping_timeout,
    sse_ping,
)


@pytest.mark.asyncio
async def test_sse_ping_stores_explicit_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = AsyncMock()
    now = 100.0
    monkeypatch.setattr("backend.api.routes.time.time", lambda: now)
    connection_id = str(uuid.uuid4())

    response = await sse_ping(
        SSEPingRequest(connection_id=connection_id),
        redis=redis,
        current_user={"sub": "user-1"},
    )

    assert response == {"ok": True}
    redis.setex.assert_awaited_once()
    key, ttl, deadline = redis.setex.await_args.args
    assert key == f"{SSE_PING_KEY_PREFIX}{connection_id}"
    assert ttl == SSE_PING_TTL
    assert float(deadline) == pytest.approx(now + SSE_PING_TIMEOUT)


@pytest.mark.asyncio
async def test_process_sse_ping_timeout_disconnects_when_deadline_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="99.0")
    monkeypatch.setattr("backend.api.routes.time.time", lambda: 100.0)

    should_disconnect = await _process_sse_ping_timeout(redis, "sse:ping:1", "conn-1")

    assert should_disconnect is True


@pytest.mark.asyncio
async def test_process_sse_ping_timeout_keeps_connection_when_deadline_is_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="120.0")
    monkeypatch.setattr("backend.api.routes.time.time", lambda: 100.0)

    should_disconnect = await _process_sse_ping_timeout(redis, "sse:ping:1", "conn-1")

    assert should_disconnect is False
