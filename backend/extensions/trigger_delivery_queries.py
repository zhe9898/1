"""Trigger delivery idempotency queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.trigger import TriggerDelivery


async def get_delivery_by_idempotency_key(
    db: AsyncSession,
    *,
    tenant_id: str,
    trigger_id: str,
    idempotency_key: str,
) -> TriggerDelivery | None:
    result = await db.execute(
        select(TriggerDelivery).where(
            TriggerDelivery.tenant_id == tenant_id,
            TriggerDelivery.trigger_id == trigger_id,
            TriggerDelivery.idempotency_key == idempotency_key,
        )
    )
    return result.scalars().first()


def delivery_definition_matches(
    delivery: TriggerDelivery,
    *,
    source_kind: str,
    reason: str | None,
    input_payload: dict[str, object],
    context: dict[str, object],
) -> bool:
    return (
        delivery.source_kind == source_kind
        and (delivery.reason or None) == (reason or None)
        and dict(delivery.input_payload or {}) == dict(input_payload or {})
        and dict(delivery.context or {}) == dict(context or {})
    )
