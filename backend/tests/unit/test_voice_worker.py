from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.workers.voice_worker import (
    CONSUMER_GROUP,
    VOICE_DLQ_STREAM,
    VOICE_INPUT_STREAM,
    _handle_voice_retry_failure,
    _voice_retry_key,
)


def _make_voice_redis() -> SimpleNamespace:
    return SimpleNamespace(
        kv=SimpleNamespace(
            incr=AsyncMock(),
            expire=AsyncMock(),
            delete=AsyncMock(),
        ),
        streams=SimpleNamespace(
            xadd=AsyncMock(),
            xack=AsyncMock(),
        ),
    )


def test_voice_retry_key_none_safe() -> None:
    assert _voice_retry_key(None) is None


def test_voice_retry_key_formats_stream_id() -> None:
    assert _voice_retry_key("1-0") == "zen70:voice:retries:1-0"


@pytest.mark.asyncio
async def test_handle_voice_retry_failure_ignores_missing_message_id() -> None:
    redis = _make_voice_redis()

    await _handle_voice_retry_failure(redis, None, RuntimeError("boom"))

    redis.kv.incr.assert_not_awaited()
    redis.streams.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_voice_retry_failure_moves_message_to_dlq_after_threshold() -> None:
    redis = _make_voice_redis()
    redis.kv.incr = AsyncMock(return_value=3)

    await _handle_voice_retry_failure(redis, "1-0", RuntimeError("boom"))

    redis.kv.incr.assert_awaited_once_with("zen70:voice:retries:1-0")
    redis.kv.expire.assert_awaited_once_with("zen70:voice:retries:1-0", 3600)
    redis.streams.xadd.assert_awaited_once()
    xadd_args, _ = redis.streams.xadd.await_args
    assert xadd_args[0] == VOICE_DLQ_STREAM
    assert xadd_args[1]["msg_id"] == "1-0"
    redis.streams.xack.assert_awaited_once_with(VOICE_INPUT_STREAM, CONSUMER_GROUP, "1-0")
    redis.kv.delete.assert_awaited_once_with("zen70:voice:retries:1-0")
