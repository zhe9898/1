from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.kernel.contracts.errors import zen
from backend.kernel.execution.job_concurrency_service import build_job_concurrency_window
from backend.kernel.extensions.job_kind_registry import assert_job_submission_authorized, validate_job_payload
from backend.kernel.scheduling.worker_pool import resolve_job_queue_contract
from backend.models.job import Job
from backend.platform.db.advisory_locks import acquire_transaction_advisory_locks
from backend.platform.db.rls import set_tenant_context
from backend.platform.redis.client import CHANNEL_JOB_EVENTS, RedisClient

from .database import _append_log, _get_job_by_idempotency_key, _job_definition_matches
from .helpers import _normalize_idempotency_key, _to_response, _utcnow
from .models import JobCreateRequest, JobResponse


@dataclass(frozen=True, slots=True)
class SubmitJobDependencies:
    assert_job_submission_authorized: Callable[[str, dict[str, object]], None]
    validate_job_payload: Callable[..., dict[str, object]]
    resolve_job_queue_contract: Callable[..., tuple[str, str]]
    acquire_transaction_advisory_locks: Callable[..., Awaitable[None]]
    check_concurrent_limits: Callable[..., Awaitable[None]]
    set_tenant_context: Callable[..., Awaitable[None]]
    get_job_by_idempotency_key: Callable[..., Awaitable[Any | None]]
    job_definition_matches: Callable[[Any, JobCreateRequest], bool]
    append_log: Callable[..., Awaitable[None]]
    publish_control_event: Callable[..., Awaitable[None]]
    normalize_idempotency_key: Callable[[str | None], str | None]
    to_response: Callable[..., JobResponse]
    utcnow: Callable[[], Any]


async def check_concurrent_limits(
    db: AsyncSession,
    tenant_id: str,
    job_type: str,
    connector_id: str | None = None,
) -> None:
    """Check if creating a new job would exceed concurrent limits."""
    concurrency_window = build_job_concurrency_window(db=db, tenant_id=tenant_id)
    await concurrency_window.assert_capacity(job_type=job_type, connector_id=connector_id)


def build_default_submit_job_dependencies() -> SubmitJobDependencies:
    return SubmitJobDependencies(
        assert_job_submission_authorized=assert_job_submission_authorized,
        validate_job_payload=validate_job_payload,
        resolve_job_queue_contract=resolve_job_queue_contract,
        acquire_transaction_advisory_locks=acquire_transaction_advisory_locks,
        check_concurrent_limits=check_concurrent_limits,
        set_tenant_context=set_tenant_context,
        get_job_by_idempotency_key=_get_job_by_idempotency_key,
        job_definition_matches=_job_definition_matches,
        append_log=_append_log,
        publish_control_event=publish_control_event,
        normalize_idempotency_key=_normalize_idempotency_key,
        to_response=_to_response,
        utcnow=_utcnow,
    )


async def _check_submission_admission(db: AsyncSession, tenant_id: str) -> None:
    from backend.kernel.scheduling.scheduling_resilience import AdmissionController, SchedulingMetrics

    admitted, admission_reason, admission_details = await AdmissionController.check_admission(
        db,
        tenant_id,
    )
    if admitted:
        return
    SchedulingMetrics.record_admission_rejection()
    raise zen(
        "ZEN-JOB-4099",
        admission_reason,
        status_code=429,
        recovery_hint="Wait for active jobs to complete before submitting new jobs",
        details=admission_details,
    )


async def _reuse_idempotent_job(
    db: AsyncSession,
    *,
    tenant_id: str,
    idempotency_key: str | None,
    payload: JobCreateRequest,
    deps: SubmitJobDependencies,
) -> JobResponse | None:
    if not idempotency_key:
        return None
    existing = await deps.get_job_by_idempotency_key(db, tenant_id, idempotency_key)
    if existing is None:
        return None
    if not deps.job_definition_matches(existing, payload):
        raise zen(
            "ZEN-JOB-4090",
            "Idempotency key already belongs to a different job definition",
            status_code=409,
            recovery_hint="Reuse the original job contract or generate a new idempotency key",
            details={"job_id": existing.job_id, "idempotency_key": idempotency_key},
        )
    return deps.to_response(existing)


def _validated_submission_contract(
    payload: JobCreateRequest,
    *,
    deps: SubmitJobDependencies,
) -> tuple[dict[str, object], str, str]:
    try:
        validated_payload = deps.validate_job_payload(payload.kind, payload.payload)
    except ValueError as exc:
        raise zen(
            "ZEN-JOB-4001",
            str(exc),
            status_code=400,
            recovery_hint="Check payload schema for job kind or register the kind if it's new",
            details={"kind": payload.kind, "payload": payload.payload},
        ) from exc

    try:
        queue_class, worker_pool = deps.resolve_job_queue_contract(
            kind=payload.kind,
            source=payload.source,
            requested_queue_class=payload.queue_class,
            requested_worker_pool=payload.worker_pool,
            required_gpu_vram_mb=payload.required_gpu_vram_mb,
        )
    except ValueError as exc:
        raise zen(
            "ZEN-JOB-4002",
            str(exc),
            status_code=400,
            recovery_hint="Use a supported queue class or worker pool contract",
            details={
                "queue_class": payload.queue_class,
                "worker_pool": payload.worker_pool,
                "kind": payload.kind,
            },
        ) from exc

    return validated_payload, queue_class, worker_pool


def _build_job_record(
    payload: JobCreateRequest,
    *,
    tenant_id: str,
    created_by: str,
    idempotency_key: str | None,
    validated_payload: dict[str, object],
    queue_class: str,
    worker_pool: str,
    now: object,
) -> Job:
    return Job(
        tenant_id=tenant_id,
        job_id=str(uuid.uuid4()),
        kind=payload.kind,
        connector_id=payload.connector_id,
        idempotency_key=idempotency_key,
        priority=payload.priority,
        queue_class=queue_class,
        worker_pool=worker_pool,
        target_os=payload.target_os,
        target_arch=payload.target_arch,
        target_executor=payload.target_executor,
        required_capabilities=payload.required_capabilities,
        target_zone=payload.target_zone,
        required_cpu_cores=payload.required_cpu_cores,
        required_memory_mb=payload.required_memory_mb,
        required_gpu_vram_mb=payload.required_gpu_vram_mb,
        required_storage_mb=payload.required_storage_mb,
        timeout_seconds=payload.timeout_seconds,
        max_retries=payload.max_retries,
        retry_count=0,
        estimated_duration_s=payload.estimated_duration_s,
        source=payload.source,
        created_by=created_by,
        payload=validated_payload,
        lease_seconds=payload.lease_seconds,
        status="pending",
        attempt=0,
        attempt_count=0,
        created_at=now,
        updated_at=now,
        data_locality_key=payload.data_locality_key,
        max_network_latency_ms=payload.max_network_latency_ms,
        prefer_cached_data=int(payload.prefer_cached_data),
        power_budget_watts=payload.power_budget_watts,
        thermal_sensitivity=payload.thermal_sensitivity,
        cloud_fallback_enabled=int(payload.cloud_fallback_enabled),
        preferred_device_profile=payload.preferred_device_profile,
        scheduling_strategy=payload.scheduling_strategy,
        affinity_labels=payload.affinity_labels,
        affinity_rule=payload.affinity_rule,
        anti_affinity_key=payload.anti_affinity_key,
        parent_job_id=payload.parent_job_id,
        depends_on=payload.depends_on,
        gang_id=payload.gang_id,
        batch_key=payload.batch_key,
        preemptible=int(payload.preemptible),
        deadline_at=payload.deadline_at,
        sla_seconds=payload.sla_seconds,
    )


async def _lock_and_guard_submission_capacity(
    db: AsyncSession,
    *,
    tenant_id: str,
    job: Job,
    deps: SubmitJobDependencies,
) -> str:
    from backend.kernel.execution.job_type_separation import apply_job_type_defaults, get_job_type

    apply_job_type_defaults(job)
    job_type = get_job_type(job)
    lock_specs = [
        ("jobs.submit.global", (job_type,)),
        ("jobs.submit.tenant", (job_type, tenant_id)),
    ]
    if job.connector_id:
        lock_specs.append(("jobs.submit.connector", (job_type, tenant_id, job.connector_id)))
    await deps.acquire_transaction_advisory_locks(db, lock_specs)
    await deps.check_concurrent_limits(db, tenant_id, job_type, connector_id=job.connector_id)
    return job_type


async def _flush_job_or_recover_idempotency(
    db: AsyncSession,
    *,
    job: Job,
    tenant_id: str,
    idempotency_key: str | None,
    payload: JobCreateRequest,
    deps: SubmitJobDependencies,
) -> JobResponse | None:
    db.add(job)
    try:
        await db.flush()
        return None
    except Exception:
        if not idempotency_key:
            raise
        try:
            await db.rollback()
            await deps.set_tenant_context(db, tenant_id)
        except Exception as recovery_exc:
            raise zen(
                "ZEN-JOB-5033",
                "Failed to recover transaction state after idempotency conflict",
                status_code=503,
                recovery_hint="Retry the request after database connectivity is restored",
                details={"tenant_id": tenant_id, "idempotency_key": idempotency_key},
            ) from recovery_exc
        existing = await deps.get_job_by_idempotency_key(db, tenant_id, idempotency_key)
        if existing is None or not deps.job_definition_matches(existing, payload):
            raise zen(
                "ZEN-JOB-4090",
                "Idempotency key already belongs to a different job definition",
                status_code=409,
                recovery_hint="Reuse the original job contract or generate a new idempotency key",
                details={"idempotency_key": idempotency_key},
            )
        return deps.to_response(existing)


async def execute_job_submission(
    payload: JobCreateRequest,
    *,
    current_user: dict[str, object],
    db: AsyncSession,
    redis: RedisClient | None,
    deps: SubmitJobDependencies,
) -> JobResponse:
    idempotency_key = deps.normalize_idempotency_key(payload.idempotency_key)
    created_by = str(current_user.get("sub") or current_user.get("username") or "unknown")
    tenant_id = str(current_user.get("tenant_id") or "default")
    now = deps.utcnow()

    deps.assert_job_submission_authorized(payload.kind, current_user)
    await _check_submission_admission(db, tenant_id)
    existing_job = await _reuse_idempotent_job(
        db,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload=payload,
        deps=deps,
    )
    if existing_job is not None:
        return existing_job

    validated_payload, queue_class, worker_pool = _validated_submission_contract(payload, deps=deps)
    job = _build_job_record(
        payload,
        tenant_id=tenant_id,
        created_by=created_by,
        idempotency_key=idempotency_key,
        validated_payload=validated_payload,
        queue_class=queue_class,
        worker_pool=worker_pool,
        now=now,
    )
    await _lock_and_guard_submission_capacity(db, tenant_id=tenant_id, job=job, deps=deps)
    recovered_job = await _flush_job_or_recover_idempotency(
        db,
        job=job,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        payload=payload,
        deps=deps,
    )
    if recovered_job is not None:
        return recovered_job

    await deps.append_log(
        db,
        job.job_id,
        (
            f"job created source={job.source} priority={job.priority} "
            f"queue_class={job.queue_class} worker_pool={job.worker_pool} "
            f"selectors=os:{job.target_os or '*'} arch:{job.target_arch or '*'} executor:{job.target_executor or '*'} "
            f"zone:{job.target_zone or '*'} cpu:{job.required_cpu_cores or '*'} memory:{job.required_memory_mb or '*'} "
            f"gpu:{job.required_gpu_vram_mb or '*'} storage:{job.required_storage_mb or '*'}"
        ),
        tenant_id=job.tenant_id,
    )
    response = deps.to_response(job)
    await deps.publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "created",
        {"job": response.model_dump(mode="json")},
    )
    return response


async def submit_job(
    payload: JobCreateRequest,
    *,
    current_user: dict[str, object],
    db: AsyncSession,
    redis: RedisClient | None,
) -> JobResponse:
    return await execute_job_submission(
        payload,
        current_user=current_user,
        db=db,
        redis=redis,
        deps=build_default_submit_job_dependencies(),
    )
