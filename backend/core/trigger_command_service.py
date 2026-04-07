from __future__ import annotations

import datetime
import uuid
from typing import Any

from backend.core.compatibility_adapter import canonicalize_status
from backend.models.trigger import Trigger, TriggerDelivery


class TriggerCommandService:
    @staticmethod
    def upsert_trigger(
        trigger: Trigger | None,
        *,
        tenant_id: str,
        trigger_id: str,
        name: str,
        description: str | None,
        kind: str,
        status: str,
        config: dict[str, Any],
        input_defaults: dict[str, Any],
        target: dict[str, Any],
        actor: str,
        now: datetime.datetime,
    ) -> tuple[Trigger, str]:
        canonical_status = canonicalize_status("triggers.status", status)
        if trigger is None:
            return (
                Trigger(
                    tenant_id=tenant_id,
                    trigger_id=trigger_id,
                    name=name,
                    description=description,
                    kind=kind,
                    status=canonical_status,
                    config=config,
                    input_defaults=input_defaults,
                    target=target,
                    created_by=actor,
                    updated_by=actor,
                    created_at=now,
                    updated_at=now,
                ),
                "upserted",
            )
        trigger.name = name
        trigger.description = description
        trigger.kind = kind
        trigger.status = canonical_status
        trigger.config = config
        trigger.input_defaults = input_defaults
        trigger.target = target
        trigger.updated_by = actor
        trigger.updated_at = now
        return trigger, "updated"

    @staticmethod
    def set_status(
        trigger: Trigger,
        *,
        status: str,
        actor: str,
        now: datetime.datetime,
        reason: str | None = None,
    ) -> None:
        trigger.status = canonicalize_status("triggers.status", status)
        trigger.updated_by = actor
        trigger.updated_at = now
        if reason:
            trigger.last_delivery_message = reason

    @staticmethod
    def create_delivery(
        trigger: Trigger,
        *,
        actor: str,
        source_kind: str,
        input_payload: dict[str, object],
        context: dict[str, object],
        reason: str | None,
        idempotency_key: str | None,
        now: datetime.datetime,
    ) -> TriggerDelivery:
        return TriggerDelivery(
            tenant_id=trigger.tenant_id,
            delivery_id=str(uuid.uuid4()),
            trigger_id=trigger.trigger_id,
            trigger_kind=trigger.kind,
            source_kind=source_kind,
            status="dispatching",
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            input_payload=input_payload,
            context=context,
            fired_at=now,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def mark_delivery_failed(
        trigger: Trigger,
        delivery: TriggerDelivery,
        *,
        message: str,
        fired_at: datetime.datetime,
        failed_at: datetime.datetime,
    ) -> None:
        delivery.status = "failed"
        delivery.error_message = message
        delivery.delivered_at = failed_at
        trigger.last_fired_at = fired_at
        trigger.last_delivery_status = "failed"
        trigger.last_delivery_message = message
        trigger.last_delivery_id = delivery.delivery_id
        trigger.last_delivery_target_kind = None
        trigger.last_delivery_target_id = None
        trigger.updated_at = failed_at

    @staticmethod
    def mark_delivery_accepted(
        trigger: Trigger,
        delivery: TriggerDelivery,
        *,
        target_kind: str,
        target_id: str,
        target_snapshot: dict[str, Any],
        message: str,
        fired_at: datetime.datetime,
        accepted_at: datetime.datetime,
    ) -> None:
        delivery.status = "accepted"
        delivery.target_kind = target_kind
        delivery.target_id = target_id
        delivery.target_snapshot = target_snapshot
        delivery.error_message = None
        delivery.delivered_at = accepted_at
        trigger.last_fired_at = fired_at
        trigger.last_delivery_status = "accepted"
        trigger.last_delivery_message = message
        trigger.last_delivery_id = delivery.delivery_id
        trigger.last_delivery_target_kind = target_kind
        trigger.last_delivery_target_id = target_id
        trigger.updated_at = accepted_at
