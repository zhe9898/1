"""Unified trigger control-plane APIs."""

from __future__ import annotations

import datetime
import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import _bind_tenant_db, get_current_admin, get_db, get_redis, get_tenant_db
from backend.kernel.contracts.status import canonicalize_status, normalize_persisted_status
from backend.kernel.contracts.errors import zen
from backend.platform.redis.client import CHANNEL_TRIGGER_EVENTS, RedisClient
from backend.kernel.extensions.trigger_command_service import TriggerCommandService
from backend.kernel.extensions.trigger_kind_registry import (
    ManualTriggerConfig,
    WebhookTriggerConfig,
    get_trigger_kind_info,
    list_trigger_kinds,
    validate_trigger_config,
)
from backend.kernel.extensions.trigger_service import fire_trigger, validate_trigger_target_contract
from backend.platform.http.webhooks import verify_timestamped_hmac_sha256
from backend.models.trigger import Trigger, TriggerDelivery

router = APIRouter(prefix="/api/v1/triggers", tags=["triggers"])


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class TriggerKindResponse(BaseModel):
    kind: str
    has_config_schema: bool | None = None
    config_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TriggerUpsertRequest(BaseModel):
    trigger_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    kind: str = Field(..., min_length=1, max_length=64)
    status: str = Field(default="active", min_length=1, max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
    input_defaults: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)


class TriggerStatusRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class TriggerFireRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class TriggerResponse(BaseModel):
    trigger_id: str
    name: str
    description: str | None
    kind: str
    status: str
    config: dict[str, Any] = Field(default_factory=dict)
    input_defaults: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    last_fired_at: datetime.datetime | None = None
    last_delivery_status: str | None = None
    last_delivery_message: str | None = None
    last_delivery_id: str | None = None
    last_delivery_target_kind: str | None = None
    last_delivery_target_id: str | None = None
    next_run_at: datetime.datetime | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class TriggerDeliveryResponse(BaseModel):
    delivery_id: str
    trigger_id: str
    trigger_kind: str
    source_kind: str
    status: str
    idempotency_key: str | None = None
    actor: str | None = None
    reason: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    target_kind: str | None = None
    target_id: str | None = None
    target_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    fired_at: datetime.datetime
    delivered_at: datetime.datetime | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def _to_trigger_response(trigger: Trigger) -> TriggerResponse:
    return TriggerResponse(
        trigger_id=trigger.trigger_id,
        name=trigger.name,
        description=trigger.description,
        kind=trigger.kind,
        status=normalize_persisted_status("triggers.status", trigger.status) or "active",
        config=dict(trigger.config or {}),
        input_defaults=dict(trigger.input_defaults or {}),
        target=dict(trigger.target or {}),
        last_fired_at=trigger.last_fired_at,
        last_delivery_status=normalize_persisted_status("trigger_deliveries.status", trigger.last_delivery_status),
        last_delivery_message=trigger.last_delivery_message,
        last_delivery_id=trigger.last_delivery_id,
        last_delivery_target_kind=trigger.last_delivery_target_kind,
        last_delivery_target_id=trigger.last_delivery_target_id,
        next_run_at=trigger.next_run_at,
        created_by=trigger.created_by,
        updated_by=trigger.updated_by,
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
    )


def _to_delivery_response(delivery: TriggerDelivery) -> TriggerDeliveryResponse:
    return TriggerDeliveryResponse(
        delivery_id=delivery.delivery_id,
        trigger_id=delivery.trigger_id,
        trigger_kind=delivery.trigger_kind,
        source_kind=delivery.source_kind,
        status=normalize_persisted_status("trigger_deliveries.status", delivery.status) or "dispatching",
        idempotency_key=delivery.idempotency_key,
        actor=delivery.actor,
        reason=delivery.reason,
        input_payload=dict(delivery.input_payload or {}),
        context=dict(delivery.context or {}),
        target_kind=delivery.target_kind,
        target_id=delivery.target_id,
        target_snapshot=dict(delivery.target_snapshot or {}),
        error_message=delivery.error_message,
        fired_at=delivery.fired_at,
        delivered_at=delivery.delivered_at,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
    )


def _assert_manual_api_fire_allowed(trigger: Trigger) -> None:
    if trigger.kind != "manual":
        recovery_hint = "Use the trigger's native ingress contract instead of the manual fire API"
        if trigger.kind == "webhook":
            recovery_hint = "Use /api/v1/triggers/webhooks/{tenant_id}/{trigger_id} for webhook ingress"
        elif trigger.kind == "cron":
            recovery_hint = "Fire cron triggers through the scheduler/runtime instead of the manual fire API"
        elif trigger.kind == "event":
            recovery_hint = "Fire event triggers through the event bus ingress instead of the manual fire API"
        raise zen(
            "ZEN-TRIG-4094",
            "Trigger kind is not eligible for manual API fire",
            status_code=409,
            recovery_hint=recovery_hint,
            details={"trigger_id": trigger.trigger_id, "kind": trigger.kind},
        )

    try:
        manual_config = ManualTriggerConfig.model_validate(validate_trigger_config("manual", trigger.config))
    except ValueError as exc:
        raise zen(
            "ZEN-TRIG-4003",
            str(exc),
            status_code=400,
            recovery_hint="Update the trigger config to satisfy the manual ingress contract",
            details={"trigger_id": trigger.trigger_id, "kind": trigger.kind},
        ) from exc
    if not manual_config.allow_api_fire:
        raise zen(
            "ZEN-TRIG-4095",
            "Manual API fire is disabled for this trigger",
            status_code=409,
            recovery_hint="Set manual.allow_api_fire=true or use a different trigger ingress",
            details={"trigger_id": trigger.trigger_id, "kind": trigger.kind},
        )


async def _get_trigger_for_tenant(db: AsyncSession, tenant_id: str, trigger_id: str) -> Trigger:
    result = await db.execute(
        select(Trigger).where(
            Trigger.tenant_id == tenant_id,
            Trigger.trigger_id == trigger_id,
        )
    )
    trigger = result.scalars().first()
    if trigger is None:
        raise zen(
            "ZEN-TRIG-4040",
            "Trigger not found",
            status_code=404,
            recovery_hint="Refresh the trigger list and retry",
            details={"trigger_id": trigger_id},
        )
    return trigger


@router.get("/kinds", response_model=list[TriggerKindResponse])
async def list_registered_trigger_kinds(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> list[TriggerKindResponse]:
    del current_user
    return [TriggerKindResponse(**item) for item in list_trigger_kinds()]


@router.get("/kinds/{kind:path}", response_model=TriggerKindResponse)
async def get_registered_trigger_kind(
    kind: str,
    current_user: dict[str, object] = Depends(get_current_admin),
) -> TriggerKindResponse:
    del current_user
    try:
        return TriggerKindResponse(**get_trigger_kind_info(kind))
    except ValueError as exc:
        raise zen("ZEN-TRIG-4041", str(exc), status_code=404) from exc


@router.post("", response_model=TriggerResponse)
async def upsert_trigger(
    payload: TriggerUpsertRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> TriggerResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    actor = str(current_user.get("sub") or current_user.get("username") or "unknown")
    try:
        validated_config = validate_trigger_config(payload.kind, payload.config)
        validated_target = validate_trigger_target_contract(payload.target)
    except ValueError as exc:
        raise zen(
            "ZEN-TRIG-4001",
            str(exc),
            status_code=400,
            recovery_hint="Validate trigger kind config and target contract before retrying",
        ) from exc
    now = _utcnow()

    result = await db.execute(
        select(Trigger).where(
            Trigger.tenant_id == tenant_id,
            Trigger.trigger_id == payload.trigger_id,
        )
    )
    trigger = result.scalars().first()

    trigger, action = TriggerCommandService.upsert_trigger(
        trigger,
        tenant_id=tenant_id,
        trigger_id=payload.trigger_id,
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        status=payload.status,
        config=validated_config,
        input_defaults=dict(payload.input_defaults),
        target=validated_target,
        actor=actor,
        now=now,
    )
    if action == "upserted":
        db.add(trigger)

    await db.flush()
    response = _to_trigger_response(trigger)
    await db.commit()
    await publish_control_event(
        redis,
        CHANNEL_TRIGGER_EVENTS,
        action,
        {"trigger": response.model_dump(mode="json")},
    )
    return response


@router.get("", response_model=list[TriggerResponse])
async def list_triggers(
    kind: str | None = None,
    status: str | None = None,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[TriggerResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    query = select(Trigger).where(Trigger.tenant_id == tenant_id)
    if kind:
        query = query.where(Trigger.kind == kind)
    if status:
        query = query.where(Trigger.status == canonicalize_status("triggers.status", status))
    result = await db.execute(query.order_by(desc(Trigger.updated_at)))
    return [_to_trigger_response(item) for item in result.scalars().all()]


@router.get("/{trigger_id}", response_model=TriggerResponse)
async def get_trigger(
    trigger_id: str,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> TriggerResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    trigger = await _get_trigger_for_tenant(db, tenant_id, trigger_id)
    return _to_trigger_response(trigger)


@router.post("/{trigger_id}/activate", response_model=TriggerResponse)
async def activate_trigger(
    trigger_id: str,
    payload: TriggerStatusRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> TriggerResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    actor = str(current_user.get("sub") or current_user.get("username") or "unknown")
    trigger = await _get_trigger_for_tenant(db, tenant_id, trigger_id)
    now = _utcnow()
    TriggerCommandService.set_status(trigger, status="active", actor=actor, now=now, reason=payload.reason)
    await db.flush()
    response = _to_trigger_response(trigger)
    await db.commit()
    await publish_control_event(redis, CHANNEL_TRIGGER_EVENTS, "activated", {"trigger": response.model_dump(mode="json")})
    return response


@router.post("/{trigger_id}/pause", response_model=TriggerResponse)
async def pause_trigger(
    trigger_id: str,
    payload: TriggerStatusRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> TriggerResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    actor = str(current_user.get("sub") or current_user.get("username") or "unknown")
    trigger = await _get_trigger_for_tenant(db, tenant_id, trigger_id)
    now = _utcnow()
    TriggerCommandService.set_status(trigger, status="inactive", actor=actor, now=now, reason=payload.reason)
    await db.flush()
    response = _to_trigger_response(trigger)
    await db.commit()
    await publish_control_event(redis, CHANNEL_TRIGGER_EVENTS, "paused", {"trigger": response.model_dump(mode="json")})
    return response


@router.post("/{trigger_id}/fire", response_model=TriggerDeliveryResponse)
async def fire_trigger_endpoint(
    trigger_id: str,
    payload: TriggerFireRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> TriggerDeliveryResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    actor = str(current_user.get("sub") or current_user.get("username") or "unknown")
    trigger = await _get_trigger_for_tenant(db, tenant_id, trigger_id)
    _assert_manual_api_fire_allowed(trigger)
    delivery = await fire_trigger(
        db,
        trigger=trigger,
        actor=actor,
        redis=redis,
        source_kind="manual",
        input_payload=payload.input,
        context=payload.context,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
    )
    return _to_delivery_response(delivery)


@router.get("/{trigger_id}/deliveries", response_model=list[TriggerDeliveryResponse])
async def list_trigger_deliveries(
    trigger_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[TriggerDeliveryResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    await _get_trigger_for_tenant(db, tenant_id, trigger_id)
    result = await db.execute(
        select(TriggerDelivery)
        .where(
            TriggerDelivery.tenant_id == tenant_id,
            TriggerDelivery.trigger_id == trigger_id,
        )
        .order_by(desc(TriggerDelivery.fired_at))
        .limit(limit)
    )
    return [_to_delivery_response(item) for item in result.scalars().all()]


@router.post("/webhooks/{tenant_id}/{trigger_id}", response_model=TriggerDeliveryResponse)
async def receive_trigger_webhook(
    tenant_id: str,
    trigger_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisClient | None = Depends(get_redis),
) -> TriggerDeliveryResponse:
    db = await _bind_tenant_db(db, tenant_id)
    trigger = await _get_trigger_for_tenant(db, tenant_id, trigger_id)
    if trigger.kind != "webhook":
        raise zen(
            "ZEN-TRIG-4093",
            "Trigger is not configured for webhook ingress",
            status_code=409,
            recovery_hint="Use the generic fire API or reconfigure the trigger kind",
            details={"trigger_id": trigger_id, "kind": trigger.kind},
        )

    webhook_config = WebhookTriggerConfig.model_validate(validate_trigger_config("webhook", trigger.config))
    if request.method.upper() not in webhook_config.accepted_methods:
        raise zen(
            "ZEN-TRIG-4050",
            "Webhook method not allowed",
            status_code=405,
            recovery_hint="Retry with an allowed webhook method",
            details={"allowed_methods": webhook_config.accepted_methods},
        )

    body = await request.body()
    if webhook_config.secret:
        provided_signature = request.headers.get(webhook_config.signature_header, "")
        provided_timestamp = request.headers.get(webhook_config.timestamp_header, "")
        try:
            verify_timestamped_hmac_sha256(
                secret=webhook_config.secret,
                body=body,
                signature=provided_signature,
                timestamp=provided_timestamp,
                tolerance_seconds=webhook_config.max_signature_age_seconds,
            )
        except ValueError as exc:
            raise zen(
                "ZEN-TRIG-4010",
                "Webhook signature verification failed",
                status_code=401,
                recovery_hint="Recompute the HMAC-SHA256 signature over '<timestamp>.<body>' and retry",
                details={
                    "trigger_id": trigger_id,
                    "signature_header": webhook_config.signature_header,
                    "timestamp_header": webhook_config.timestamp_header,
                    "reason": str(exc),
                },
            ) from exc

    try:
        payload_raw = json.loads(body.decode("utf-8"))
    except Exception:
        payload_raw = body.decode("utf-8", errors="replace")

    if isinstance(payload_raw, dict):
        input_payload = dict(payload_raw)
    else:
        input_payload = {"value": payload_raw}

    query_context = {key: value for key, value in request.query_params.items()}
    context: dict[str, object] = {
        "method": request.method.upper(),
        "content_type": request.headers.get("content-type"),
        "user_agent": request.headers.get("user-agent"),
        "query": query_context,
    }
    delivery = await fire_trigger(
        db,
        trigger=trigger,
        actor="webhook",
        redis=redis,
        source_kind="webhook",
        input_payload=input_payload,
        context=context,
        reason="webhook",
        idempotency_key=request.headers.get(webhook_config.idempotency_header),
    )
    return _to_delivery_response(delivery)

