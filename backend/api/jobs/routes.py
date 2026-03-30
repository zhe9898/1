import datetime
import os
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import (
    get_current_admin,
    get_current_user,
    get_machine_tenant_db,
    get_node_machine_token,
    get_redis,
    get_tenant_db,
)
from backend.api.ui_contracts import ResourceSchemaResponse, StatusView
from backend.core.control_plane_state import (
    eligibility_view,
    node_drain_status_view,
    node_status_view,
)
from backend.core.errors import zen
from backend.core.failure_control_plane import get_failure_control_plane
from backend.core.failure_taxonomy import FailureCategory, infer_failure_category, should_retry_job
from backend.core.job_kind_registry import validate_job_payload
from backend.core.job_scheduler import (
    build_node_snapshot,
    count_eligible_nodes_for_job,
    node_blockers_for_job,
    score_job_for_node,
    select_jobs_for_node,
)
from backend.core.node_auth import authenticate_node_request
from backend.core.redis_client import CHANNEL_JOB_EVENTS, RedisClient
from backend.core.rls import set_tenant_context
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt

from .database import (
    _append_log,
    _assert_valid_lease_owner,
    _build_snapshots,
    _create_attempt,
    _expire_previous_attempt_if_needed,
    _get_attempt_for_callback,
    _get_current_attempt,
    _get_job_by_id,
    _get_job_by_id_for_update,
    _get_job_by_idempotency_key,
    _job_definition_matches,
    _load_node_metrics,
    _load_recent_failed_job_ids,
    move_to_dead_letter_queue,
)
from .helpers import (
    _matches_job_list_filters,
    _new_lease_token,
    _normalize_idempotency_key,
    _to_attempt_response,
    _to_lease_response,
    _to_response,
    _utcnow,
)
from .models import (
    JobActionRequest,
    JobAttemptResponse,
    JobCreateRequest,
    JobExplainDecisionResponse,
    JobExplainResponse,
    JobFailRequest,
    JobLeaseResponse,
    JobProgressRequest,
    JobPullRequest,
    JobRenewRequest,
    JobResponse,
    JobResultRequest,
)
from .schemas import _resource_schema

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_ATTEMPT_LOOKBACK_HOURS = 24


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



@router.post("/pull", response_model=list[JobLeaseResponse])
async def pull_jobs(
    payload: JobPullRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> list[JobLeaseResponse]:
    requesting_node = await authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=True,
        tenant_id=payload.tenant_id,
    )
    now = _utcnow()

    # ── Quarantine gate ──────────────────────────────────────────────
    _fcp = get_failure_control_plane()
    if await _fcp.is_node_quarantined(payload.node_id, now=now):
        return []

    active_nodes, active_lease_counts, reliability_map = await _load_node_metrics(
        db,
        tenant_id=payload.tenant_id,
        now=now,
        only_active_enrollment=True,
    )
    node_snapshot = build_node_snapshot(
        requesting_node,
        active_lease_count=active_lease_counts.get(payload.node_id, 0),
        reliability_score=reliability_map.get(payload.node_id, 0.85),
    )
    active_node_snapshots = _build_snapshots(
        active_nodes,
        active_lease_counts=active_lease_counts,
        reliability_map=reliability_map,
    )

    accepted_kinds = set(payload.accepted_kinds)
    candidate_limit = min(max(payload.limit * 40, 40), 200)
    query = (
        select(Job)
        .where(
            Job.tenant_id == payload.tenant_id,
            or_(
                (Job.status == "pending") & (or_(Job.retry_at.is_(None), Job.retry_at <= now)),
                (Job.status == "leased") & (Job.leased_until.is_not(None)) & (Job.leased_until < now),
            ),
        )
        .with_for_update(skip_locked=True)
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(candidate_limit)
    )
    if accepted_kinds:
        query = query.where(Job.kind.in_(accepted_kinds))

    result = await db.execute(query)
    candidates = list(result.scalars().all())

    # Apply queue stratification with aging
    from backend.core.queue_stratification import sort_jobs_by_stratified_priority

    candidates = sort_jobs_by_stratified_priority(candidates, now=now, aging_enabled=True)

    # ── Phase 2: Business scheduling filters (single entry point) ────
    from backend.core.business_scheduling import apply_business_filters

    # Pre-fetch dependency and parent data needed by the filter
    all_dep_ids: set[str] = set()
    for c in candidates:
        all_dep_ids.update(c.depends_on or [])
    if all_dep_ids:
        dep_result = await db.execute(
            select(Job.job_id).where(
                Job.tenant_id == payload.tenant_id,
                Job.job_id.in_(all_dep_ids),
                Job.status == "completed",
            )
        )
        completed_dep_ids: set[str] = set(dep_result.scalars().all())
    else:
        completed_dep_ids = set()

    parent_ids = {c.parent_job_id for c in candidates if c.parent_job_id}
    if parent_ids:
        parent_result = await db.execute(
            select(Job).where(Job.tenant_id == payload.tenant_id, Job.job_id.in_(parent_ids))
        )
        parent_jobs = {j.job_id: j for j in parent_result.scalars().all()}
    else:
        parent_jobs = {}

    available_slots = max(node_snapshot.max_concurrency - node_snapshot.active_lease_count, 0)
    candidates = apply_business_filters(
        candidates,
        completed_job_ids=completed_dep_ids,
        available_slots=available_slots,
        parent_jobs=parent_jobs,
        now=now,
    )
    # ── End Phase 2 ──────────────────────────────────────────────────

    # ── Phase 3: Score, select, lease ────────────────────────────────
    recent_failed_job_ids = await _load_recent_failed_job_ids(
        db,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        job_ids=[job.job_id for job in candidates],
        now=now,
    )

    # Load active jobs on this node for anti-affinity check
    active_jobs_result = await db.execute(
        select(Job).where(
            Job.tenant_id == payload.tenant_id,
            Job.node_id == payload.node_id,
            Job.status == "leased",
        )
    )
    active_jobs_on_node = list(active_jobs_result.scalars().all())

    selected = select_jobs_for_node(
        candidates,
        node_snapshot,
        active_node_snapshots,
        now=now,
        accepted_kinds=accepted_kinds,
        recent_failed_job_ids=recent_failed_job_ids,
        active_jobs_on_node=active_jobs_on_node,
        limit=payload.limit,
    )

    leased_jobs: list[Job] = []
    for scored in selected:
        job = scored.job
        await _expire_previous_attempt_if_needed(db, job, now=now)
        job.status = "leased"
        job.node_id = payload.node_id
        job.attempt = int(job.attempt or 0) + 1
        job.attempt_count = int(getattr(job, "attempt_count", 0) or 0) + 1
        job.lease_token = _new_lease_token()
        job.result = None
        job.error_message = None
        job.completed_at = None
        job.started_at = now
        job.leased_until = now + datetime.timedelta(seconds=job.lease_seconds)
        job.updated_at = now
        await db.flush()
        await _create_attempt(db, job=job, node_id=payload.node_id, score=scored.score, now=now)
        await _append_log(
            db,
            job.job_id,
            f"job leased by {payload.node_id} attempt={job.attempt} score={scored.score} eligible_nodes={scored.eligible_nodes_count}",
            tenant_id=job.tenant_id,
        )
        leased_jobs.append(job)

    responses = [_to_lease_response(job, now=now) for job in leased_jobs]
    if responses:
        await publish_control_event(
            redis,
            CHANNEL_JOB_EVENTS,
            "leased",
            {
                "node_id": payload.node_id,
                "jobs": [_to_response(job, now=now).model_dump(mode="json") for job in leased_jobs],
            },
        )
    return responses


@router.post("/{id}/result", response_model=JobResponse)
async def complete_job(
    id: str,
    payload: JobResultRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    """Mark job as completed with row-level lock to prevent race conditions."""
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "result")
    if job.status == "completed":
        return _to_response(job)

    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting terminal state",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()
    attempt.status = "completed"
    attempt.result_summary = payload.result
    attempt.error_message = None
    attempt.completed_at = now
    attempt.updated_at = now
    job.status = "completed"
    job.result = payload.result
    job.error_message = None
    job.completed_at = now
    job.leased_until = None
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.log or f"job completed by {payload.node_id} attempt={payload.attempt}",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "completed",
        {"job": response.model_dump(mode="json")},
    )

    # Reset node failure counter on success
    _fcp = get_failure_control_plane()
    await _fcp.record_success(node_id=payload.node_id, now=now)

    return response


@router.post("/{id}/fail", response_model=JobResponse)
async def fail_job(
    id: str,
    payload: JobFailRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    """Mark job as failed with row-level lock to prevent race conditions."""
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "fail")
    if job.status == "failed":
        return _to_response(job)

    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting terminal state",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()

    # Infer failure category if not provided
    failure_category_str = payload.failure_category or infer_failure_category(
        error_message=payload.error,
        exit_code=payload.error_details.get("exit_code") if payload.error_details else None,
        error_details=payload.error_details,
    ).value

    # ── Failure control plane: track node/connector/kind failures ────
    _fcp = get_failure_control_plane()
    await _fcp.record_failure(
        node_id=payload.node_id,
        job_id=job.job_id,
        category=failure_category_str,
        connector_id=getattr(job, "connector_id", None),
        kind=job.kind,
        now=now,
    )

    attempt.status = "failed"
    attempt.error_message = payload.error
    attempt.failure_category = failure_category_str
    attempt.completed_at = now
    attempt.updated_at = now

    # Decide if should retry based on failure category
    failure_category = FailureCategory(failure_category_str)
    should_retry = should_retry_job(job, failure_category)

    if should_retry:
        # Calculate retry delay with exponential backoff based on failure category
        from backend.core.failure_taxonomy import calculate_retry_delay_seconds

        retry_delay_seconds = calculate_retry_delay_seconds(
            failure_category,
            int(job.retry_count or 0),
            base_delay=int(os.getenv("RETRY_BASE_DELAY_SECONDS", "10")),
            max_delay=int(os.getenv("RETRY_MAX_DELAY_SECONDS", "600")),
        )
        retry_at = now + datetime.timedelta(seconds=retry_delay_seconds)

        job.retry_count = int(job.retry_count or 0) + 1
        job.status = "pending"
        job.node_id = None
        job.lease_token = None
        job.error_message = payload.error
        job.failure_category = failure_category_str
        job.completed_at = None
        job.started_at = None
        job.leased_until = None
        job.retry_at = retry_at
        job.updated_at = now
        await db.flush()
        await _append_log(
            db,
            job.job_id,
            payload.log
            or f"job failed on {payload.node_id}; requeued retry={job.retry_count}/{job.max_retries} category={failure_category_str} retry_at={retry_at.isoformat()}",
            level="warning",
            tenant_id=job.tenant_id,
        )
        response = _to_response(job, now=now)
        await publish_control_event(
            redis,
            CHANNEL_JOB_EVENTS,
            "requeued",
            {"job": response.model_dump(mode="json"), "failure_category": failure_category_str},
        )
        await db.commit()
        return response

    job.status = "failed"
    job.error_message = payload.error
    job.failure_category = failure_category_str
    job.completed_at = now
    job.leased_until = None
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.log or f"job failed permanently on {payload.node_id} category={failure_category_str}",
        level="error",
        tenant_id=job.tenant_id,
    )

    # Move to dead-letter queue
    await move_to_dead_letter_queue(redis, db, job)

    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "failed",
        {"job": response.model_dump(mode="json"), "failure_category": failure_category_str, "will_retry": False},
    )
    await db.commit()
    return response


@router.post("/{id}/progress", response_model=JobResponse)
async def report_job_progress(
    id: str,
    payload: JobProgressRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)
    job = await _get_job_by_id(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "progress")
    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting progress",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()
    attempt.status = "running"
    attempt.result_summary = {"progress": payload.progress, "message": payload.message}
    attempt.updated_at = now
    job.started_at = job.started_at or now
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.log or f"progress={payload.progress}% node={payload.node_id} message={payload.message or '-'}",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "progress",
        {"job": response.model_dump(mode="json")},
    )
    return response


@router.post("/{id}/renew", response_model=JobLeaseResponse)
async def renew_job_lease(
    id: str,
    payload: JobRenewRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobLeaseResponse:
    """Renew job lease with row-level lock to prevent race conditions."""
    await authenticate_node_request(db, payload.node_id, node_token, require_active=True, tenant_id=payload.tenant_id)

    # CRITICAL: Use row-level lock to prevent race with lease expiration
    job = await _get_job_by_id_for_update(db, payload.tenant_id, id)

    _assert_valid_lease_owner(job, payload, "renew")
    attempt = await _get_attempt_for_callback(db, job, payload)
    if attempt is None:
        raise zen(
            "ZEN-JOB-4093",
            "Job attempt history is missing for this lease",
            status_code=409,
            recovery_hint="Pull a fresh job lease before renewing",
            details={"job_id": job.job_id, "node_id": payload.node_id, "attempt": payload.attempt},
        )

    now = _utcnow()
    attempt.status = "running"
    attempt.updated_at = now
    job.leased_until = now + datetime.timedelta(seconds=payload.extend_seconds)
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.log or f"lease renewed by {payload.node_id} extend={payload.extend_seconds}s",
        tenant_id=job.tenant_id,
    )
    response = _to_lease_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "renewed",
        {"job": _to_response(job, now=now).model_dump(mode="json")},
    )
    return response


@router.post("/{id}/cancel", response_model=JobResponse)
async def cancel_job(
    id: str,
    payload: JobActionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    job = await _get_job_by_id(db, tenant_id, id)
    if job.status == "canceled":
        return _to_response(job)
    if job.status not in {"pending", "leased"}:
        raise zen(
            "ZEN-JOB-4094",
            "Only pending or leased jobs can be canceled",
            status_code=409,
            recovery_hint="Retry only after the job returns to a cancelable state",
            details={"job_id": job.job_id, "status": job.status},
        )

    now = _utcnow()
    attempt = await _get_current_attempt(db, job)
    if attempt is not None and attempt.status in {"leased", "running"}:
        attempt.status = "canceled"
        attempt.error_message = payload.reason or "canceled by operator"
        attempt.completed_at = now
        attempt.updated_at = now

    job.status = "canceled"
    job.error_message = payload.reason or "canceled by operator"
    job.leased_until = None
    job.lease_token = None
    job.completed_at = now
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.reason or "job canceled by operator",
        level="warning",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "canceled",
        {"job": response.model_dump(mode="json")},
    )
    return response


@router.post("/{id}/retry", response_model=JobResponse)
async def retry_job_now(
    id: str,
    payload: JobActionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    job = await _get_job_by_id(db, tenant_id, id)
    if job.status == "pending":
        return _to_response(job)
    if job.status not in {"failed", "completed", "canceled"}:
        raise zen(
            "ZEN-JOB-4095",
            "Only terminal jobs can be retried manually",
            status_code=409,
            recovery_hint="Cancel or wait for the current lease before retrying",
            details={"job_id": job.job_id, "status": job.status},
        )

    now = _utcnow()
    job.status = "pending"
    job.node_id = None
    job.lease_token = None
    job.leased_until = None
    job.retry_at = None
    job.result = None
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.retry_count = 0
    job.attempt_count = 0
    job.updated_at = now
    await db.flush()
    await _append_log(
        db,
        job.job_id,
        payload.reason or "job queued for manual retry",
        level="warning",
        tenant_id=job.tenant_id,
    )
    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "manual-retry",
        {"job": response.model_dump(mode="json")},
    )
    return response


@router.get("/{id}/explain", response_model=JobExplainResponse)
async def explain_job(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobExplainResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    now = _utcnow()
    job = await _get_job_by_id(db, tenant_id, id)

    nodes, active_lease_counts, reliability_map = await _load_node_metrics(db, tenant_id=tenant_id, now=now)
    snapshots = _build_snapshots(
        nodes,
        active_lease_counts=active_lease_counts,
        reliability_map=reliability_map,
    )
    failed_nodes_result = await db.execute(
        select(JobAttempt.node_id)
        .where(
            JobAttempt.tenant_id == tenant_id,
            JobAttempt.job_id == job.job_id,
            JobAttempt.status == "failed",
            JobAttempt.created_at >= now - datetime.timedelta(hours=_ATTEMPT_LOOKBACK_HOURS),
        )
        .distinct()
    )
    recent_failed_node_ids = {str(node_id) for node_id in failed_nodes_result.scalars().all() if node_id}
    eligible_nodes = count_eligible_nodes_for_job(job, snapshots, now=now)
    total_active_nodes = sum(1 for snapshot in snapshots if snapshot.enrollment_status == "active")

    # Same lease snapshot as pull_jobs: anti-affinity penalty uses active jobs per node
    leased_rows = await db.execute(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.status == "leased",
        )
    )
    active_by_node: dict[str, list[Job]] = defaultdict(list)
    for leased in leased_rows.scalars().all():
        nid = getattr(leased, "node_id", None)
        if nid:
            active_by_node[str(nid)].append(leased)

    decisions: list[JobExplainDecisionResponse] = []
    for snapshot in snapshots:
        reasons = node_blockers_for_job(job, snapshot, now=now)
        eligible = not reasons
        score: int | None = None
        if eligible:
            score = score_job_for_node(
                job,
                snapshot,
                now=now,
                total_active_nodes=total_active_nodes,
                eligible_nodes_count=eligible_nodes,
                recent_failed_job_ids={job.job_id} if snapshot.node_id in recent_failed_node_ids else set(),
                active_jobs_on_node=active_by_node.get(snapshot.node_id, []),
            )
        decisions.append(
            JobExplainDecisionResponse(
                node_id=snapshot.node_id,
                eligible=eligible,
                eligibility_view=StatusView(**eligibility_view(eligible)),
                score=score,
                reasons=reasons,
                active_lease_count=snapshot.active_lease_count,
                max_concurrency=snapshot.max_concurrency,
                executor=snapshot.executor,
                os=snapshot.os,
                arch=snapshot.arch,
                zone=snapshot.zone,
                cpu_cores=snapshot.cpu_cores,
                memory_mb=snapshot.memory_mb,
                gpu_vram_mb=snapshot.gpu_vram_mb,
                storage_mb=snapshot.storage_mb,
                drain_status=snapshot.drain_status,
                drain_status_view=StatusView(**node_drain_status_view(snapshot.drain_status)),
                reliability_score=round(snapshot.reliability_score, 4),
                status=snapshot.status,
                status_view=StatusView(**node_status_view(snapshot.status)),
                last_seen_at=snapshot.last_seen_at,
            )
        )
    decisions.sort(key=lambda item: (not item.eligible, -(item.score or -10_000), item.node_id))
    return JobExplainResponse(
        job=_to_response(job, now=now),
        total_nodes=len(snapshots),
        eligible_nodes=eligible_nodes,
        selected_node_id=job.node_id,
        decisions=decisions,
    )


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

