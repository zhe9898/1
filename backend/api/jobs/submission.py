from __future__ import annotations

import uuid

from sqlalchemy import and_, func, literal, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.core.db_locks import acquire_transaction_advisory_locks
from backend.core.errors import zen
from backend.core.job_kind_registry import assert_job_submission_authorized, validate_job_payload
from backend.core.redis_client import CHANNEL_JOB_EVENTS, RedisClient
from backend.core.rls import set_tenant_context
from backend.core.worker_pool import resolve_job_queue_contract
from backend.models.job import Job

from .database import (
    _append_log,
    _get_job_by_idempotency_key,
    _job_definition_matches,
)
from .helpers import _normalize_idempotency_key, _to_response, _utcnow
from .models import JobCreateRequest, JobResponse


async def check_concurrent_limits(
    db: AsyncSession,
    tenant_id: str,
    job_type: str,
    connector_id: str | None = None,
) -> None:
    """Check if creating a new job would exceed concurrent limits."""
    from backend.core.job_type_separation import (
        SCHEDULED_JOB_SOURCES,
        format_concurrent_limit_error,
        get_max_concurrent_limit,
    )

    if job_type == "scheduled":
        source_filter = Job.source.in_(list(SCHEDULED_JOB_SOURCES))
    else:
        source_filter = ~Job.source.in_(list(SCHEDULED_JOB_SOURCES))

    connector_count_expr = func.count().filter(and_(Job.tenant_id == tenant_id, Job.connector_id == connector_id)) if connector_id else literal(0)
    counts_stmt = select(
        func.public.zen70_global_leased_jobs_count(job_type).label("global_count"),
        func.count().filter(Job.tenant_id == tenant_id).label("tenant_count"),
        connector_count_expr.label("connector_count"),
    ).where(
        Job.status == "leased",
        source_filter,
    )
    try:
        counts_row = (await db.execute(counts_stmt)).one()
        global_count = int(counts_row.global_count or 0)
        tenant_count = int(counts_row.tenant_count or 0)
        connector_count = int(counts_row.connector_count or 0)
    except (SQLAlchemyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise zen(
            "ZEN-JOB-5032",
            "Global concurrent limit function is unavailable",
            status_code=503,
            recovery_hint="Apply the latest Alembic migrations before accepting job submissions",
            details={"job_type": job_type, "migration_required": True},
        ) from exc

    global_limit = get_max_concurrent_limit(job_type, "global")
    if global_count >= global_limit:
        raise zen(
            "ZEN-JOB-4096",
            format_concurrent_limit_error(job_type, global_count, global_limit, "global"),
            status_code=429,
            recovery_hint="Wait for running jobs to complete or contact administrator",
            details={"job_type": job_type, "current": global_count, "limit": global_limit},
        )

    tenant_limit = get_max_concurrent_limit(job_type, "per_tenant")
    if tenant_count >= tenant_limit:
        raise zen(
            "ZEN-JOB-4097",
            format_concurrent_limit_error(job_type, tenant_count, tenant_limit, "per_tenant"),
            status_code=429,
            recovery_hint="Wait for running jobs to complete or contact administrator",
            details={"job_type": job_type, "tenant_id": tenant_id, "current": tenant_count, "limit": tenant_limit},
        )

    if connector_id:
        connector_limit = get_max_concurrent_limit(job_type, "per_connector")
        if connector_count >= connector_limit:
            raise zen(
                "ZEN-JOB-4098",
                format_concurrent_limit_error(job_type, connector_count, connector_limit, "per_connector"),
                status_code=429,
                recovery_hint="Wait for running jobs to complete or contact administrator",
                details={
                    "job_type": job_type,
                    "connector_id": connector_id,
                    "current": connector_count,
                    "limit": connector_limit,
                },
            )


async def submit_job(
    payload: JobCreateRequest,
    *,
    current_user: dict[str, object],
    db: AsyncSession,
    redis: RedisClient | None,
) -> JobResponse:
    idempotency_key = _normalize_idempotency_key(payload.idempotency_key)
    created_by = str(current_user.get("sub") or current_user.get("username") or "unknown")
    tenant_id = str(current_user.get("tenant_id") or "default")
    now = _utcnow()

    assert_job_submission_authorized(payload.kind, current_user)

    from backend.core.scheduling_resilience import AdmissionController, SchedulingMetrics

    admitted, admission_reason, admission_details = await AdmissionController.check_admission(
        db,
        tenant_id,
    )
    if not admitted:
        SchedulingMetrics.record_admission_rejection()
        raise zen(
            "ZEN-JOB-4099",
            admission_reason,
            status_code=429,
            recovery_hint="Wait for active jobs to complete before submitting new ones",
            details=admission_details,
        )

    if idempotency_key:
        existing = await _get_job_by_idempotency_key(db, tenant_id, idempotency_key)
        if existing is not None:
            if not _job_definition_matches(existing, payload):
                raise zen(
                    "ZEN-JOB-4090",
                    "Idempotency key already belongs to a different job definition",
                    status_code=409,
                    recovery_hint="Reuse the original job contract or generate a new idempotency key",
                    details={"job_id": existing.job_id, "idempotency_key": idempotency_key},
                )
            return _to_response(existing)

    try:
        validated_payload = validate_job_payload(payload.kind, payload.payload)
    except ValueError as exc:
        raise zen(
            "ZEN-JOB-4001",
            str(exc),
            status_code=400,
            recovery_hint="Check payload schema for job kind or register the kind if it's new",
            details={"kind": payload.kind, "payload": payload.payload},
        ) from exc

    try:
        queue_class, worker_pool = resolve_job_queue_contract(
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

    job = Job(
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

    from backend.core.job_type_separation import apply_job_type_defaults, get_job_type

    apply_job_type_defaults(job)
    job_type = get_job_type(job)
    lock_specs = [
        ("jobs.submit.global", (job_type,)),
        ("jobs.submit.tenant", (job_type, tenant_id)),
    ]
    if job.connector_id:
        lock_specs.append(("jobs.submit.connector", (job_type, tenant_id, job.connector_id)))
    await acquire_transaction_advisory_locks(db, lock_specs)
    await check_concurrent_limits(db, tenant_id, job_type, connector_id=job.connector_id)

    db.add(job)
    try:
        await db.flush()
    except Exception:
        if not idempotency_key:
            raise
        try:
            await db.rollback()
            await set_tenant_context(db, tenant_id)
        except Exception as recovery_exc:
            raise zen(
                "ZEN-JOB-5033",
                "Failed to recover transaction state after idempotency conflict",
                status_code=503,
                recovery_hint="Retry the request after database connectivity is restored",
                details={"tenant_id": tenant_id, "idempotency_key": idempotency_key},
            ) from recovery_exc
        existing = await _get_job_by_idempotency_key(db, tenant_id, idempotency_key)
        if existing is None or not _job_definition_matches(existing, payload):
            raise zen(
                "ZEN-JOB-4090",
                "Idempotency key already belongs to a different job definition",
                status_code=409,
                recovery_hint="Reuse the original job contract or generate a new idempotency key",
                details={"idempotency_key": idempotency_key},
            )
        return _to_response(existing)

    await _append_log(
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
    response = _to_response(job)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "created",
        {"job": response.model_dump(mode="json")},
    )
    return response
