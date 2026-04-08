"""
ZEN70 Voice Worker - 语音流处理与重试/DLQ 驱动。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("zen70.voice_worker")

VOICE_INPUT_STREAM = "zen70:voice:input"
VOICE_DLQ_STREAM = "zen70:voice:dlq"
CONSUMER_GROUP = "zen70-voice-cg"
MAX_VOICE_RETRIES = 3


def _voice_retry_key(message_id: str | None) -> str | None:
    if message_id is None:
        return None
    return f"zen70:voice:retries:{message_id}"


async def _handle_voice_retry_failure(
    redis: Any,
    message_id: str | None,
    error: Exception,
) -> None:
    if message_id is None:
        return

    retry_key = _voice_retry_key(message_id)
    if retry_key is None:
        return

    count = await redis.kv.incr(retry_key)
    await redis.kv.expire(retry_key, 3600)

    if count >= MAX_VOICE_RETRIES:
        # Move to DLQ
        await redis.streams.xadd(
            VOICE_DLQ_STREAM,
            {"msg_id": message_id, "error": str(error)},
        )
        await redis.streams.xack(VOICE_INPUT_STREAM, CONSUMER_GROUP, message_id)
        await redis.kv.delete(retry_key)
