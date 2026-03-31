"""
ZEN70 Jobs API – CRUD endpoints and job schema.

Split from the monolithic routes.py. Contains: create_job, list_jobs,
get_job, list_job_attempts, get_job_schema, and _check_concurrent_limits.
Dispatch (pull/explain) lives in dispatch.py; lifecycle callbacks in lifecycle.py.
"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import (
    get_current_user,
    get_redis,
    get_tenant_db,
)
from backend.api.ui_contracts import ResourceSchemaResponse
from backend.core.errors import zen
from backend.core.job_kind_registry import validate_job_payload
from backend.core.redis_client import CHANNEL_JOB_EVENTS, RedisClient
from backend.core.rls import set_tenant_context
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt

from .database import (
    _append_log,
    _get_job_by_id,
    _get_job_by_idempotency_key,
    _job_definition_matches,
)
from .helpers import (
    _matches_job_list_filters,
    _normalize_idempotency_key,
    _to_attempt_response,
    _to_response,
    _utcnow,
)
from .models import (
    JobAttemptResponse,
    JobCreateRequest,
    JobResponse,
)
from .schemas import _resource_schema

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


async def _check_concurrent_limits(
    db: AsyncSession,
    tenant_id: str,
    job_type: str,
    connector_id: str | None = None,
) -> None:
    """Check if creating a new job would exceed concurrent limits.

    Raises zen error if limits would be exceeded.
    """
    from backend.core.job_type_separation import (
        SCHEDULED_JOB_SOURCES,
        format_concurrent_limit_error,
        get_max_concurrent_limit,
    )

    # Count current leased jobs of this type
    if job_type == "scheduled":
        source_filter = Job.source.in_(list(SCHEDULED_JOB_SOURCES))
    else:
        source_filter = ~Job.source.in_(list(SCHEDULED_JOB_SOURCES))

    # Check global limit
    global_count_stmt = select(func.count()).where(
        Job.status == "leased",
        source_filter,
    )
    global_count = (await db.execute(global_count_stmt)).scalar() or 0
    global_limit = get_max_concurrent_limit(job_type, "global")

    if global_count >= global_limit:
        raise zen(
            "ZEN-JOB-4096",
            format_concurrent_limit_error(job_type, global_count, global_limit, "global"),
            status_code=429,
            recovery_hint="Wait for running jobs to complete or contact administrator",
            details={"job_type": job_type, "current": global_count, "limit": global_limit},
        )

    # Check per-tenant limit
    tenant_count_stmt = select(func.count()).where(
        Job.tenant_id == tenant_id,
        Job.status == "leased",
        source_filter,
    )
    tenant_count = (await db.execute(tenant_count_stmt)).scalar() or 0
    tenant_limit = get_max_concurrent_limit(job_type, "per_tenant")

    if tenant_count >= tenant_limit:
        raise zen(
            "ZEN-JOB-4097",
            format_concurrent_limit_error(job_type, tenant_count, tenant_limit, "per_tenant"),
            status_code=429,
            recovery_hint="Wait for running jobs to complete or contact administrator",
            details={"job_type": job_type, "tenant_id": tenant_id, "current": tenant_count, "limit": tenant_limit},
        )

    # Check per-connector limit (if connector_id is provided)
    if connector_id:
        connector_count_stmt = select(func.count()).where(
            Job.tenant_id == tenant_id,
            Job.connector_id == connector_id,
            Job.status == "leased",
            source_filter,
        )
        connector_count = (await db.execute(connector_count_stmt)).scalar() or 0
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


@router.get("/schema", response_model=ResourceSchemaResponse)
async def get_job_schema(
    current_user: dict[str, object] = Depends(get_current_user),
) -> ResourceSchemaResponse:
    del current_user
    return _resource_schema()


@router.post("", response_model=JobResponse)
async def create_job(
    payload: JobCreateRequest,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    idempotency_key = _normalize_idempotency_key(payload.idempotency_key)
    created_by = str(current_user.get("sub") or current_user.get("username") or "unknown")
    tenant_id = str(current_user.get("tenant_id") or "default")

    # ── Admission control (queue depth backpressure) ───────────────
    from backend.core.scheduling_resilience import AdmissionController, SchedulingMetrics

    admitted, admission_reason, admission_details = await AdmissionController.check_admission(
        db, tenant_id,
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

    # Validate payload against registered schema
    try:
        validated_payload = validate_job_payload(payload.kind, payload.payload)
    except ValueError as e:
        raise zen(
            "ZEN-JOB-4001",
            str(e),
            status_code=400,
            recovery_hint="Check payload schema for job kind or register the kind if it's new",
            details={"kind": payload.kind, "payload": payload.payload},
        ) from e

    job = Job(
        tenant_id=tenant_id,
        job_id=str(uuid.uuid4()),
        kind=payload.kind,
        connector_id=payload.connector_id,
        idempotency_key=idempotency_key,
        priority=payload.priority,
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
        # Edge computing
        data_locality_key=payload.data_locality_key,
        max_network_latency_ms=payload.max_network_latency_ms,
        prefer_cached_data=int(payload.prefer_cached_data),
        power_budget_watts=payload.power_budget_watts,
        thermal_sensitivity=payload.thermal_sensitivity,
        cloud_fallback_enabled=int(payload.cloud_fallback_enabled),
        # Scheduling strategy and affinity
        scheduling_strategy=payload.scheduling_strategy,
        affinity_labels=payload.affinity_labels,
        affinity_rule=payload.affinity_rule,
        anti_affinity_key=payload.anti_affinity_key,
        # Business scheduling
        parent_job_id=payload.parent_job_id,
        depends_on=payload.depends_on,
        gang_id=payload.gang_id,
        batch_key=payload.batch_key,
        preemptible=int(payload.preemptible),
        deadline_at=payload.deadline_at,
        sla_seconds=payload.sla_seconds,
    )

    # Apply job type defaults (scheduled vs background)
    from backend.core.job_type_separation import apply_job_type_defaults, get_job_type

    apply_job_type_defaults(job)

    # Check concurrent limits before creating job
    job_type = get_job_type(job)
    await _check_concurrent_limits(db, tenant_id, job_type, connector_id=job.connector_id)

    db.add(job)
    try:
        await db.flush()
    except Exception:
        if not idempotency_key:
            raise
        await db.rollback()
        await set_tenant_context(db, tenant_id)
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



@router.get("", response_model=list[JobResponse])
async def list_jobs(
    job_id: str | None = None,
    status: str | None = None,
    lease_state: str | None = None,
    priority_bucket: str | None = None,
    target_executor: str | None = None,
    target_zone: str | None = None,
    required_capability: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[JobResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    query = select(Job).where(Job.tenant_id == tenant_id)
    if job_id:
        query = query.where(Job.job_id == job_id)
    if target_executor:
        query = query.where(Job.target_executor == target_executor)
    if target_zone:
        query = query.where(Job.target_zone == target_zone)
    result = await db.execute(query.order_by(Job.priority.desc(), Job.created_at.desc()))
    now = _utcnow()
    jobs = [
        job
        for job in result.scalars().all()
        if _matches_job_list_filters(
            job,
            now=now,
            status=status,
            lease_state=lease_state,
            priority_bucket=priority_bucket,
            target_executor=target_executor,
            target_zone=target_zone,
            required_capability=required_capability,
        )
    ]
    return [_to_response(job, now=now) for job in jobs]


@router.get("/{id}", response_model=JobResponse)
async def get_job(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    now = _utcnow()
    job = await _get_job_by_id(db, tenant_id, id)
    return _to_response(job, now=now)


@router.get("/{id}/attempts", response_model=list[JobAttemptResponse])
async def list_job_attempts(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[JobAttemptResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    result = await db.execute(
        select(JobAttempt)
        .where(JobAttempt.tenant_id == tenant_id, JobAttempt.job_id == id)
        .order_by(JobAttempt.attempt_no.desc(), JobAttempt.created_at.desc())
    )
    return [_to_attempt_response(attempt) for attempt in result.scalars().all()]


# ============================================================================
# Dead-Letter Queue API Endpoints
# ============================================================================

