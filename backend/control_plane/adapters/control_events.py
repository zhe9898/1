from __future__ import annotations

import datetime
import json
import logging
import time
import uuid
from typing import Any

from backend.platform.events.channels import control_plane_publish_subjects, is_tenant_scoped_realtime_channel
from backend.platform.events.runtime import get_runtime_event_bus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


async def publish_control_event(
    channel: str,
    action: str,
    payload: dict[str, Any],
    *,
    tenant_id: str | None = None,
) -> None:
    """
    Publish control-plane SSE event.
    Failure is non-blocking: API write path should not fail because of event bus issue.
    """
    event_bus = get_runtime_event_bus()
    if event_bus is None:
        return
    if tenant_id is not None:
        tenant_id = str(tenant_id).strip() or None
    if is_tenant_scoped_realtime_channel(channel) and tenant_id is None:
        raise ValueError(f"tenant-scoped control-plane event '{channel}' must include tenant_id")
    subjects = control_plane_publish_subjects(channel, tenant_id=tenant_id)

    message = {
        "event_id": str(uuid.uuid4()),
        "revision": time.time_ns(),
        "action": action,
        "ts": _now_iso(),
        **({"tenant_id": tenant_id} if tenant_id is not None else {}),
        **payload,
    }
    encoded_message = json.dumps(message, ensure_ascii=False)
    for subject in subjects:
        try:
            await event_bus.publish(subject, encoded_message)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug(
                "publish_control_event failed subject=%s channel=%s action=%s err=%s",
                subject,
                channel,
                action,
                exc,
            )
