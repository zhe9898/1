from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.platform.events.channels import control_plane_publish_subjects, is_tenant_scoped_realtime_channel


@dataclass(frozen=True, slots=True)
class ControlEventPublishContract:
    channel: str
    action: str
    payload: dict[str, Any]
    tenant_id: str | None
    subjects: tuple[str, ...]


def build_control_event_publish_contract(
    channel: str,
    action: str,
    payload: dict[str, Any],
    *,
    tenant_id: str | None = None,
) -> ControlEventPublishContract:
    normalized_channel = str(channel).strip()
    if not normalized_channel:
        raise ValueError("control event channel is required")

    normalized_action = str(action).strip()
    if not normalized_action:
        raise ValueError("control event action is required")

    normalized_tenant_id = None
    if tenant_id is not None:
        normalized_tenant_id = str(tenant_id).strip() or None
    if is_tenant_scoped_realtime_channel(normalized_channel) and normalized_tenant_id is None:
        raise ValueError(f"tenant-scoped control-plane event '{normalized_channel}' must include tenant_id")

    return ControlEventPublishContract(
        channel=normalized_channel,
        action=normalized_action,
        payload=payload,
        tenant_id=normalized_tenant_id,
        subjects=control_plane_publish_subjects(normalized_channel, tenant_id=normalized_tenant_id),
    )
