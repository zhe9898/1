"""Trigger dispatch service for the unified control-plane trigger layer."""

from __future__ import annotations

import datetime
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.jobs.models import JobCreateRequest
from backend.api.jobs.submission_service import submit_job
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.status import normalize_persisted_status
from backend.kernel.extensions.extension_sdk import bootstrap_extension_runtime, get_published_job_kind, get_published_workflow_template
from backend.kernel.extensions.trigger_command_service import TriggerCommandService
from backend.kernel.extensions.workflow_engine import create_workflow
from backend.kernel.extensions.workflow_template_registry import render_workflow_template
from backend.models.trigger import Trigger, TriggerDelivery
from backend.platform.redis.client import CHANNEL_TRIGGER_EVENTS, RedisClient


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class JobTriggerTarget(BaseModel):
    target_kind: Literal["job"] = "job"
    job_kind: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, object] = Field(default_factory=dict)
    connector_id: str | None = None
    lease_seconds: int = Field(default=30, ge=5, le=3600)
    priority: int = Field(default=50, ge=0, le=100)
    queue_class: str | None = Field(default=None, pattern="^(realtime|interactive|batch|gpu-heavy|analytics)$")
    worker_pool: str | None = Field(default=None, pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,63})$")
    source: str | None = Field(default=None, min_length=1, max_length=64)
    target_os: str | None = Field(default=None, min_length=1, max_length=64)
    target_arch: str | None = Field(default=None, min_length=1, max_length=64)
    target_executor: str | None = Field(default=None, min_length=1, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list)
    target_zone: str | None = Field(default=None, min_length=1, max_length=128)
    required_cpu_cores: int | None = Field(default=None, ge=1, le=4096)
    required_memory_mb: int | None = Field(default=None, ge=1, le=8_388_608)
    required_gpu_vram_mb: int | None = Field(default=None, ge=1, le=8_388_608)
    required_storage_mb: int | None = Field(default=None, ge=1, le=134_217_728)
    timeout_seconds: int = Field(default=300, ge=5, le=86_400)
    max_retries: int = Field(default=0, ge=0, le=10)
    estimated_duration_s: int | None = Field(default=None, ge=1, le=86_400)
    data_locality_key: str | None = Field(default=None, max_length=255)
    max_network_latency_ms: int | None = Field(default=None, ge=1, le=60_000)
    prefer_cached_data: bool = False
    power_budget_watts: int | None = Field(default=None, ge=1, le=10_000)
    thermal_sensitivity: str | None = Field(default=None, pattern="^(low|normal|high)$")
    cloud_fallback_enabled: bool = False
    scheduling_strategy: str | None = Field(default=None, pattern="^(spread|binpack|locality|performance|balanced)$")
    affinity_labels: dict[str, str] = Field(default_factory=dict)
    affinity_rule: str | None = Field(default=None, pattern="^(required|preferred)$")
    anti_affinity_key: str | None = Field(default=None, max_length=128)
    parent_job_id: str | None = Field(default=None, max_length=128)
    depends_on: list[str] = Field(default_factory=list)
    gang_id: str | None = Field(default=None, max_length=128)
    batch_key: str | None = Field(default=None, max_length=128)
    preemptible: bool = True
    deadline_at: datetime.datetime | None = None
    sla_seconds: int | None = Field(default=None, ge=1, le=86_400 * 7)


class WorkflowTemplateTriggerTarget(BaseModel):
    target_kind: Literal["workflow_template"] = "workflow_template"
    template_id: str = Field(..., min_length=1, max_length=128)
    parameters: dict[str, object] = Field(default_factory=dict)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


TriggerTargetContract = Annotated[JobTriggerTarget | WorkflowTemplateTriggerTarget, Field(discriminator="target_kind")]


def validate_trigger_target_contract(target: dict[str, Any]) -> dict[str, Any]:
    target_kind = str(target.get("target_kind") or "").strip()
    if target_kind == "job":
        parsed_job = JobTriggerTarget.model_validate(target)
        bootstrap_extension_runtime()
        get_published_job_kind(parsed_job.job_kind)
        parsed: JobTriggerTarget | WorkflowTemplateTriggerTarget = parsed_job
    elif target_kind == "workflow_template":
        parsed_template = WorkflowTemplateTriggerTarget.model_validate(target)
        parsed = parsed_template
    else:
        raise ValueError("Trigger target_kind must be 'job' or 'workflow_template'")
    if isinstance(parsed, WorkflowTemplateTriggerTarget):
        bootstrap_extension_runtime()
        get_published_workflow_template(parsed.template_id)
    return parsed.model_dump(mode="json")


def _delivery_event_payload(trigger: Trigger, delivery: TriggerDelivery) -> dict[str, Any]:
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


async def _dispatch_job_target(
    db: AsyncSession,
    redis: RedisClient | None,
    trigger: Trigger,
    actor: str,
    target: JobTriggerTarget,
    *,
    input_payload: dict[str, object],
) -> tuple[str, str, dict[str, Any], str]:
    merged_payload = {
        **dict(target.payload or {}),
        **dict(trigger.input_defaults or {}),
        **dict(input_payload or {}),
    }
    response = await submit_job(
        JobCreateRequest(
            kind=target.job_kind,
            payload=merged_payload,
            connector_id=target.connector_id,
            lease_seconds=target.lease_seconds,
            priority=target.priority,
            queue_class=target.queue_class,
            worker_pool=target.worker_pool,
            target_os=target.target_os,
            target_arch=target.target_arch,
            target_executor=target.target_executor,
            required_capabilities=target.required_capabilities,
            target_zone=target.target_zone,
            required_cpu_cores=target.required_cpu_cores,
            required_memory_mb=target.required_memory_mb,
            required_gpu_vram_mb=target.required_gpu_vram_mb,
            required_storage_mb=target.required_storage_mb,
            timeout_seconds=target.timeout_seconds,
            max_retries=target.max_retries,
            estimated_duration_s=target.estimated_duration_s,
            source=target.source or f"trigger:{trigger.trigger_id}",
            data_locality_key=target.data_locality_key,
            max_network_latency_ms=target.max_network_latency_ms,
            prefer_cached_data=target.prefer_cached_data,
            power_budget_watts=target.power_budget_watts,
            thermal_sensitivity=target.thermal_sensitivity,
            cloud_fallback_enabled=target.cloud_fallback_enabled,
            scheduling_strategy=target.scheduling_strategy,
            affinity_labels=target.affinity_labels,
            affinity_rule=target.affinity_rule,
            anti_affinity_key=target.anti_affinity_key,
            parent_job_id=target.parent_job_id,
            depends_on=target.depends_on,
            gang_id=target.gang_id,
            batch_key=target.batch_key,
            preemptible=target.preemptible,
            deadline_at=target.deadline_at,
            sla_seconds=target.sla_seconds,
        ),
        current_user={"tenant_id": trigger.tenant_id, "sub": actor, "username": actor},
        db=db,
        redis=redis,
    )
    return "job", response.job_id, response.model_dump(mode="json"), "job accepted"


async def _dispatch_workflow_template_target(
    db: AsyncSession,
    trigger: Trigger,
    actor: str,
    target: WorkflowTemplateTriggerTarget,
    *,
    input_payload: dict[str, object],
) -> tuple[str, str, dict[str, Any], str]:
    bootstrap_extension_runtime()
    merged_parameters = {
        **dict(target.parameters or {}),
        **dict(trigger.input_defaults or {}),
        **dict(input_payload or {}),
    }
    rendered = render_workflow_template(target.template_id, merged_parameters)
    workflow = await create_workflow(
        db,
        tenant_id=trigger.tenant_id,
        name=target.name or rendered["display_name"],
        description=target.description or rendered["description"],
        steps=rendered["steps"],
        created_by=actor,
    )
    snapshot = {
        "workflow_id": workflow.workflow_id,
        "name": workflow.name,
        "status": normalize_persisted_status("workflows.status", workflow.status),
        "steps_count": len(workflow.steps or []),
    }
    return "workflow", workflow.workflow_id, snapshot, "workflow accepted"


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

    normalized_input = dict(input_payload or {})
    normalized_context = dict(context or {})
    normalized_reason = (reason or "").strip() or None
    normalized_idempotency_key = (idempotency_key or "").strip() or None

    if normalized_idempotency_key:
        existing = await get_delivery_by_idempotency_key(
            db,
            tenant_id=trigger.tenant_id,
            trigger_id=trigger.trigger_id,
            idempotency_key=normalized_idempotency_key,
        )
        if existing is not None:
            if not delivery_definition_matches(
                existing,
                source_kind=source_kind,
                reason=normalized_reason,
                input_payload=normalized_input,
                context=normalized_context,
            ):
                raise zen(
                    "ZEN-TRIG-4092",
                    "Idempotency key already belongs to a different trigger delivery",
                    status_code=409,
                    recovery_hint="Reuse the original delivery contract or generate a new idempotency key",
                    details={"trigger_id": trigger.trigger_id, "idempotency_key": normalized_idempotency_key},
                )
            return existing

    now = _utcnow()
    delivery = TriggerCommandService.create_delivery(
        trigger,
        actor=actor,
        source_kind=source_kind,
        input_payload=normalized_input,
        context=normalized_context,
        reason=normalized_reason,
        idempotency_key=normalized_idempotency_key,
        now=now,
    )
    db.add(delivery)
    await db.flush()

    try:
        target_contract = validate_trigger_target_contract(dict(trigger.target or {}))
        if target_contract["target_kind"] == "job":
            target_kind, target_id, target_snapshot, message = await _dispatch_job_target(
                db,
                redis,
                trigger,
                actor,
                JobTriggerTarget.model_validate(target_contract),
                input_payload=normalized_input,
            )
        else:
            target_kind, target_id, target_snapshot, message = await _dispatch_workflow_template_target(
                db,
                trigger,
                actor,
                WorkflowTemplateTriggerTarget.model_validate(target_contract),
                input_payload=normalized_input,
            )
    except HTTPException as exc:
        failed_at = _utcnow()
        TriggerCommandService.mark_delivery_failed(
            trigger,
            delivery,
            message=str(exc.detail),
            fired_at=now,
            failed_at=failed_at,
        )
        await db.flush()
        await db.commit()
        await publish_control_event(CHANNEL_TRIGGER_EVENTS, "delivery_failed", _delivery_event_payload(trigger, delivery))
        raise
    except ValueError as exc:
        failed_at = _utcnow()
        TriggerCommandService.mark_delivery_failed(
            trigger,
            delivery,
            message=str(exc),
            fired_at=now,
            failed_at=failed_at,
        )
        await db.flush()
        await db.commit()
        await publish_control_event(CHANNEL_TRIGGER_EVENTS, "delivery_failed", _delivery_event_payload(trigger, delivery))
        raise zen(
            "ZEN-TRIG-4002",
            str(exc),
            status_code=400,
            recovery_hint="Validate trigger input against the downstream job or workflow contract",
            details={"trigger_id": trigger.trigger_id},
        ) from exc
    except Exception as exc:
        failed_at = _utcnow()
        TriggerCommandService.mark_delivery_failed(
            trigger,
            delivery,
            message=str(exc),
            fired_at=now,
            failed_at=failed_at,
        )
        await db.flush()
        await db.commit()
        await publish_control_event(CHANNEL_TRIGGER_EVENTS, "delivery_failed", _delivery_event_payload(trigger, delivery))
        raise zen(
            "ZEN-TRIG-5001",
            "Trigger delivery failed",
            status_code=500,
            recovery_hint="Inspect trigger history and downstream target contract",
            details={"trigger_id": trigger.trigger_id, "error": str(exc)},
        ) from exc

    delivered_at = _utcnow()
    TriggerCommandService.mark_delivery_delivered(
        trigger,
        delivery,
        target_kind=target_kind,
        target_id=target_id,
        target_snapshot=target_snapshot,
        message=message,
        fired_at=now,
        delivered_at=delivered_at,
    )
    await db.flush()
    await db.commit()
    await publish_control_event(CHANNEL_TRIGGER_EVENTS, "fired", _delivery_event_payload(trigger, delivery))
    return delivery
