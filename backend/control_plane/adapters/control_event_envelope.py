from __future__ import annotations

import datetime
import time
import uuid
from typing import Any

from backend.platform.events.channels import CONTROL_EVENT_ENVELOPE_RESERVED_FIELDS


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()


def validate_control_event_payload(payload: dict[str, Any]) -> None:
    reserved_keys = sorted(key for key in CONTROL_EVENT_ENVELOPE_RESERVED_FIELDS if key in payload)
    if reserved_keys:
        raise ValueError(f"control event payload must not override reserved envelope fields: {reserved_keys}")


def build_control_event_message(
    action: str,
    payload: dict[str, Any],
    *,
    tenant_id: str | None,
) -> dict[str, Any]:
    normalized_action = str(action).strip()
    if not normalized_action:
        raise ValueError("control event action is required")
    validate_control_event_payload(payload)
    message: dict[str, Any] = {
        **payload,
        "event_id": str(uuid.uuid4()),
        "revision": time.time_ns(),
        "action": normalized_action,
        "ts": _now_iso(),
    }
    if tenant_id is not None:
        message["tenant_id"] = tenant_id
    return message
