"""Trigger delivery mutation and event publication helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.extensions.trigger_command_service import TriggerCommandService
from backend.kernel.contracts.status import normalize_persisted_status
from backend.models.trigger import Trigger, TriggerDelivery


def build_delivery_event_payload(trigger: Trigger, delivery: TriggerDelivery) -> dict[str, Any]:
    return {
        "trigger": {
            "trigger_id": trigger.trigger_id,
            "kind": trigger.kind,
            "status": normalize_persisted_status("triggers.status", trigger.status),
            "last_delivery_status": normalize_persisted_status("trigger_deliveries.status", trigger.last_delivery_status),
            "last_delivery_id": trigger.last_delivery_id,
            "last_delivery_target_kind": trigger.last_delivery_target_kind,
            "last_delivery_target_id": trigger.last_delivery_target_id,
        },
        "delivery": {
            "delivery_id": delivery.delivery_id,
            "status": normalize_persisted_status("trigger_deliveries.status", delivery.status),
            "source_kind": delivery.source_kind,
            "target_kind": delivery.target_kind,
            "target_id": delivery.target_id,
            "error_message": delivery.error_message,
            "fired_at": delivery.fired_at.isoformat(),
            "delivered_at": delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        },
    }


async def mark_delivery_failed_and_publish(
    db: AsyncSession,
    *,
    trigger: Trigger,
    delivery: TriggerDelivery,
    publish_event: Callable[..., Awaitable[None]],
    event_channel: str,
    message: str,
    fired_at,
    failed_at,
) -> None:
    TriggerCommandService.mark_delivery_failed(
        trigger,
        delivery,
        message=message,
        fired_at=fired_at,
        failed_at=failed_at,
    )
    await db.flush()
    await db.commit()
    await publish_event(
        event_channel,
        "delivery_failed",
        build_delivery_event_payload(trigger, delivery),
        tenant_id=trigger.tenant_id,
    )


async def mark_delivery_delivered_and_publish(
    db: AsyncSession,
    *,
    trigger: Trigger,
    delivery: TriggerDelivery,
    publish_event: Callable[..., Awaitable[None]],
    event_channel: str,
    target_kind: str,
    target_id: str,
    target_snapshot: dict[str, Any],
    message: str,
    fired_at,
    delivered_at,
) -> None:
    TriggerCommandService.mark_delivery_delivered(
        trigger,
        delivery,
        target_kind=target_kind,
        target_id=target_id,
        target_snapshot=target_snapshot,
        message=message,
        fired_at=fired_at,
        delivered_at=delivered_at,
    )
    await db.flush()
    await db.commit()
    await publish_event(
        event_channel,
        "fired",
        build_delivery_event_payload(trigger, delivery),
        tenant_id=trigger.tenant_id,
    )
