from __future__ import annotations

import json
from typing import Any

from backend.platform.events.runtime import get_runtime_event_bus

from .control_event_contracts import build_control_event_publish_contract
from .control_event_envelope import build_control_event_message
from .control_event_transport import publish_encoded_control_event


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

    contract = build_control_event_publish_contract(channel, action, payload, tenant_id=tenant_id)
    message = build_control_event_message(contract.action, contract.payload, tenant_id=contract.tenant_id)
    encoded_message = json.dumps(message, ensure_ascii=False)
    await publish_encoded_control_event(
        event_bus,
        subjects=contract.subjects,
        encoded_message=encoded_message,
        channel=contract.channel,
        action=contract.action,
    )
