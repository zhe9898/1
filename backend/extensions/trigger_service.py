"""Trigger dispatch orchestration for the unified control-plane trigger layer."""

from __future__ import annotations

import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.control_events import publish_control_event
from backend.control_plane.adapters.jobs.models import JobCreateRequest
from backend.control_plane.adapters.jobs.submission_service import submit_job
from backend.extensions.trigger_command_service import TriggerCommandService
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.status import normalize_persisted_status
from backend.models.trigger import Trigger, TriggerDelivery
from backend.platform.redis.client import CHANNEL_TRIGGER_EVENTS, RedisClient

from .trigger_delivery_queries import delivery_definition_matches, get_delivery_by_idempotency_key
from .trigger_delivery_runtime import mark_delivery_delivered_and_publish, mark_delivery_failed_and_publish
from .trigger_fire_contract import normalize_trigger_fire_command
from .trigger_target_dispatch import dispatch_trigger_target
from .trigger_target_validation import validate_trigger_target_contract


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


async def fire_trigger(
    db: AsyncSession,
    *,
    trigger: Trigger,
    actor: str,
    redis: RedisClient | None,
    source_kind: str,
    input_payload: dict[str, object] | None = None,
    context: dict[str, object] | None = None,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> TriggerDelivery:
    if normalize_persisted_status("triggers.status", trigger.status) != "active":
        raise zen(
            "ZEN-TRIG-4091",
            "Trigger is not active",
            status_code=409,
            recovery_hint="Activate the trigger before firing it",
            details={"trigger_id": trigger.trigger_id, "status": trigger.status},
        )

    fire_command = normalize_trigger_fire_command(
        input_payload=input_payload,
        context=context,
        reason=reason,
        idempotency_key=idempotency_key,
    )

    if fire_command.idempotency_key:
        existing = await get_delivery_by_idempotency_key(
            db,
            tenant_id=trigger.tenant_id,
            trigger_id=trigger.trigger_id,
            idempotency_key=fire_command.idempotency_key,
        )
        if existing is not None:
            if not delivery_definition_matches(
                existing,
                source_kind=source_kind,
                reason=fire_command.reason,
                input_payload=fire_command.input_payload,
                context=fire_command.context,
            ):
                raise zen(
                    "ZEN-TRIG-4092",
                    "Idempotency key already belongs to a different trigger delivery",
                    status_code=409,
                    recovery_hint="Reuse the original delivery contract or generate a new idempotency key",
                    details={"trigger_id": trigger.trigger_id, "idempotency_key": fire_command.idempotency_key},
                )
            return existing

    now = _utcnow()
    delivery = TriggerCommandService.create_delivery(
        trigger,
        actor=actor,
        source_kind=source_kind,
        input_payload=fire_command.input_payload,
        context=fire_command.context,
        reason=fire_command.reason,
        idempotency_key=fire_command.idempotency_key,
        now=now,
    )
    db.add(delivery)
    await db.flush()

    try:
        target_contract = validate_trigger_target_contract(dict(trigger.target or {}))
        target_kind, target_id, target_snapshot, message = await dispatch_trigger_target(
            db,
            redis=redis,
            trigger=trigger,
            actor=actor,
            job_request_factory=JobCreateRequest,
            submit_job_entrypoint=submit_job,
            target_contract=target_contract,
            input_payload=fire_command.input_payload,
        )
    except HTTPException as exc:
        failed_at = _utcnow()
        await mark_delivery_failed_and_publish(
            db,
            trigger=trigger,
            delivery=delivery,
            publish_event=publish_control_event,
            event_channel=CHANNEL_TRIGGER_EVENTS,
            message=str(exc.detail),
            fired_at=now,
            failed_at=failed_at,
        )
        raise
    except ValueError as exc:
        failed_at = _utcnow()
        await mark_delivery_failed_and_publish(
            db,
            trigger=trigger,
            delivery=delivery,
            publish_event=publish_control_event,
            event_channel=CHANNEL_TRIGGER_EVENTS,
            message=str(exc),
            fired_at=now,
            failed_at=failed_at,
        )
        raise zen(
            "ZEN-TRIG-4002",
            str(exc),
            status_code=400,
            recovery_hint="Validate trigger input against the downstream job or workflow contract",
            details={"trigger_id": trigger.trigger_id},
        ) from exc
    except Exception as exc:
        failed_at = _utcnow()
        await mark_delivery_failed_and_publish(
            db,
            trigger=trigger,
            delivery=delivery,
            publish_event=publish_control_event,
            event_channel=CHANNEL_TRIGGER_EVENTS,
            message=str(exc),
            fired_at=now,
            failed_at=failed_at,
        )
        raise zen(
            "ZEN-TRIG-5001",
            "Trigger delivery failed",
            status_code=500,
            recovery_hint="Inspect trigger history and downstream target contract",
            details={"trigger_id": trigger.trigger_id, "error": str(exc)},
        ) from exc

    delivered_at = _utcnow()
    await mark_delivery_delivered_and_publish(
        db,
        trigger=trigger,
        delivery=delivery,
        publish_event=publish_control_event,
        event_channel=CHANNEL_TRIGGER_EVENTS,
        target_kind=target_kind,
        target_id=target_id,
        target_snapshot=target_snapshot,
        message=message,
        fired_at=now,
        delivered_at=delivered_at,
    )
    return delivery
