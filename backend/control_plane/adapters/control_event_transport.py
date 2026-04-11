from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def publish_encoded_control_event(
    event_bus: Any,
    *,
    subjects: tuple[str, ...],
    encoded_message: str,
    channel: str,
    action: str,
) -> None:
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
