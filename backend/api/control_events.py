from __future__ import annotations

import datetime
import json
import logging
import time
import uuid
from typing import Any

from backend.platform.events.runtime import get_runtime_event_bus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


async def publish_control_event(
    channel: str,
    action: str,
    payload: dict[str, Any],
) -> None:
    """
    Publish control-plane SSE event.
    Failure is non-blocking: API write path should not fail because of event bus issue.
    """
    event_bus = get_runtime_event_bus()
    if event_bus is None:
        return

    message = {
        "event_id": str(uuid.uuid4()),
        "revision": time.time_ns(),
        "action": action,
        "ts": _now_iso(),
        **payload,
    }
    try:
        await event_bus.publish(channel, json.dumps(message, ensure_ascii=False))
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("publish_control_event failed channel=%s action=%s err=%s", channel, action, exc)
