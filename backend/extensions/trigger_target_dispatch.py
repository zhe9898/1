"""Dispatch helpers for resolved trigger targets."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.contracts.status import normalize_persisted_status
from backend.models.trigger import Trigger
from backend.platform.redis.client import RedisClient

from .trigger_target_contracts import JobTriggerTarget, WorkflowTemplateTriggerTarget
from .workflow_engine import create_workflow
from .workflow_template_registry import render_workflow_template


async def _dispatch_job_target(
    db: AsyncSession,
    redis: RedisClient | None,
    trigger: Trigger,
    actor: str,
    target: JobTriggerTarget,
    *,
    job_request_factory: Callable[..., Any],
    submit_job_entrypoint: Callable[..., Awaitable[Any]],
    input_payload: dict[str, object],
) -> tuple[str, str, dict[str, Any], str]:
    merged_payload = {
        **dict(target.payload or {}),
        **dict(trigger.input_defaults or {}),
        **dict(input_payload or {}),
    }
    response = await submit_job_entrypoint(
        job_request_factory(
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


async def dispatch_trigger_target(
    db: AsyncSession,
    *,
    redis: RedisClient | None,
    trigger: Trigger,
    actor: str,
    job_request_factory: Callable[..., Any],
    submit_job_entrypoint: Callable[..., Awaitable[Any]],
    target_contract: dict[str, Any],
    input_payload: dict[str, object],
) -> tuple[str, str, dict[str, Any], str]:
    if target_contract["target_kind"] == "job":
        return await _dispatch_job_target(
            db,
            redis,
            trigger,
            actor,
            JobTriggerTarget.model_validate(target_contract),
            job_request_factory=job_request_factory,
            submit_job_entrypoint=submit_job_entrypoint,
            input_payload=input_payload,
        )
    return await _dispatch_workflow_template_target(
        db,
        trigger,
        actor,
        WorkflowTemplateTriggerTarget.model_validate(target_contract),
        input_payload=input_payload,
    )
