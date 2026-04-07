import datetime
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import zen
from backend.core.job_scheduler import SchedulerNodeSnapshot, build_node_snapshot
from backend.core.lease_service import LeaseService
from backend.core.redis_client import RedisClient
from backend.core.worker_pool import resolve_job_queue_contract, resolve_job_queue_contract_from_record
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.job_log import JobLog
from backend.models.node import Node

from .models import JobCreateRequest, JobLeaseAckRequest


async def _append_log(
    db: AsyncSession,
    job_id: str,
    message: str,
    level: str = "info",
    *,
    tenant_id: str,
) -> None:
    db.add(JobLog(tenant_id=tenant_id, job_id=job_id, level=level, message=message))
    await db.flush()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


async def move_to_dead_letter_queue(
    redis: RedisClient | None,
    db: AsyncSession,
    job: Job,
) -> None:
    """Move failed job to dead-letter queue.

    Args:
        redis: Redis client (optional, degrades gracefully if None)
        db: Database session
        job: Failed job instance
    """
    if not redis:
        # Degraded mode: PostgreSQL is source of truth, skip Redis indexing
        await _append_log(
            db,
            job.job_id,
            f"job moved to DLQ (Redis unavailable) category={job.failure_category}",
            level="error",
            tenant_id=job.tenant_id,
        )
        return

    now = _utcnow()
    score = now.timestamp()

    # Add to Redis sorted set for fast time-based queries
    dlq_key = f"dlq:{job.tenant_id}:jobs"
    try:
        await redis.zadd(dlq_key, {job.job_id: score})  # type: ignore[attr-defined]
        # Set 90-day expiration on the DLQ key
        await redis.expire(dlq_key, 90 * 24 * 3600)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        # Redis failure should not block DLQ operation
        # PostgreSQL remains source of truth
        await _append_log(
            db,
            job.job_id,
            f"job moved to DLQ (Redis error: {exc}) category={job.failure_category}",
            level="error",
            tenant_id=job.tenant_id,
        )
        return

    await _append_log(
        db,
        job.job_id,
        f"job moved to DLQ category={job.failure_category} retry_count={job.retry_count}/{job.max_retries}",
        level="error",
        tenant_id=job.tenant_id,
    )


async def remove_from_dead_letter_queue(
    redis: RedisClient | None,
    tenant_id: str,
    job_id: str,
) -> bool:
    """Remove job from dead-letter queue.

    Args:
        redis: Redis client (optional)
        tenant_id: Tenant ID
        job_id: Job ID

    Returns:
        True if removed from Redis, False if Redis unavailable or not in DLQ
    """
    if not redis:
        return False

    dlq_key = f"dlq:{tenant_id}:jobs"
    try:
        removed = await redis.zrem(dlq_key, job_id)  # type: ignore[attr-defined]
        return bool(removed > 0)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError):
        return False


async def _get_job_by_idempotency_key(db: AsyncSession, tenant_id: str, idempotency_key: str) -> Job | None:
    result = await db.execute(select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == idempotency_key))
    return result.scalars().first()


_ATTEMPT_LOOKBACK_HOURS = 24


async def _load_node_metrics(
    db: AsyncSession,
    *,
    tenant_id: str,
    now: datetime.datetime,
    node_filter: list[str] | None = None,
    only_active_enrollment: bool = False,
) -> tuple[list[Node], dict[str, int], dict[str, float]]:
    node_query = select(Node).where(Node.tenant_id == tenant_id)
    if only_active_enrollment:
        node_query = node_query.where(Node.enrollment_status == "active")
    if node_filter:
        node_query = node_query.where(Node.node_id.in_(node_filter))
    node_result = await db.execute(node_query)
    nodes = list(node_result.scalars().all())

    lease_query = (
        select(Job.node_id, func.count())
        .where(
            Job.tenant_id == tenant_id,
            Job.node_id.is_not(None),
            Job.status == "leased",
            Job.leased_until.is_not(None),
            Job.leased_until > now,
        )
        .group_by(Job.node_id)
    )
    if node_filter:
        lease_query = lease_query.where(Job.node_id.in_(node_filter))
    lease_result = await db.execute(lease_query)
    active_lease_counts = {str(node_id): int(count or 0) for node_id, count in lease_result.all() if node_id}

    reliability_query = select(JobAttempt.node_id, JobAttempt.status).where(
        JobAttempt.tenant_id == tenant_id,
        JobAttempt.created_at >= now - datetime.timedelta(hours=_ATTEMPT_LOOKBACK_HOURS),
    )
    if node_filter:
        reliability_query = reliability_query.where(JobAttempt.node_id.in_(node_filter))
    reliability_result = await db.execute(reliability_query)
    reliability_rows = list(reliability_result.all())
    reliability_map: dict[str, float] = {}
    by_node: dict[str, list[str]] = {}
    for node_id, status in reliability_rows:
        if not node_id:
            continue
        by_node.setdefault(str(node_id), []).append(str(status))
    for node_id, statuses in by_node.items():
        reliability_map[node_id] = sum(1 for status in statuses if status == "completed") / len(statuses)
    return nodes, active_lease_counts, reliability_map


def _build_snapshots(
    nodes: list[Node],
    *,
    active_lease_counts: dict[str, int],
    reliability_map: dict[str, float],
) -> list[SchedulerNodeSnapshot]:
    return [
        build_node_snapshot(
            node,
            active_lease_count=active_lease_counts.get(node.node_id, 0),
            reliability_score=reliability_map.get(node.node_id, 0.85),
        )
        for node in nodes
    ]


def _job_definition_matches(job: Job, payload: JobCreateRequest) -> bool:
    existing_queue_class, existing_worker_pool = resolve_job_queue_contract_from_record(job)
    requested_queue_class, requested_worker_pool = resolve_job_queue_contract(
        kind=payload.kind,
        source=payload.source,
        requested_queue_class=payload.queue_class,
        requested_worker_pool=payload.worker_pool,
        required_gpu_vram_mb=payload.required_gpu_vram_mb,
    )
    return (
        job.kind == payload.kind
        and job.connector_id == payload.connector_id
        and dict(job.payload or {}) == payload.payload
        and job.lease_seconds == payload.lease_seconds
        and job.priority == payload.priority
        and existing_queue_class == requested_queue_class
        and existing_worker_pool == requested_worker_pool
        and job.target_os == payload.target_os
        and job.target_arch == payload.target_arch
        and job.target_executor == payload.target_executor
        and list(job.required_capabilities or []) == payload.required_capabilities
        and job.target_zone == payload.target_zone
        and job.required_cpu_cores == payload.required_cpu_cores
        and job.required_memory_mb == payload.required_memory_mb
        and job.required_gpu_vram_mb == payload.required_gpu_vram_mb
        and job.required_storage_mb == payload.required_storage_mb
        and job.timeout_seconds == payload.timeout_seconds
        and job.max_retries == payload.max_retries
        and job.estimated_duration_s == payload.estimated_duration_s
        and job.source == payload.source
    )


def _assert_valid_lease_owner(job: Job, payload: JobLeaseAckRequest, action: str) -> None:
    details = {
        "job_id": job.job_id,
        "status": job.status,
        "node_id": payload.node_id,
        "attempt": payload.attempt,
    }
    if job.node_id != payload.node_id or job.lease_token != payload.lease_token or job.attempt != payload.attempt:
        raise zen(
            "ZEN-JOB-4091",
            "Job lease is no longer owned by this node",
            status_code=409,
            recovery_hint="Pull a fresh job lease before reporting terminal state",
            details=details,
        )

    if action == "result" and job.status == "completed":
        return
    if action == "fail" and job.status == "failed":
        return
    if job.status != "leased":
        raise zen(
            "ZEN-JOB-4092",
            "Job is no longer accepting terminal callbacks",
            status_code=409,
            recovery_hint="Refresh control-plane state before retrying",
            details=details,
        )


async def _load_recent_failed_job_ids(
    db: AsyncSession,
    *,
    tenant_id: str,
    node_id: str,
    job_ids: list[str],
    now: datetime.datetime,
) -> set[str]:
    if not job_ids:
        return set()
    result = await db.execute(
        select(JobAttempt.job_id)
        .where(
            JobAttempt.tenant_id == tenant_id,
            JobAttempt.node_id == node_id,
            JobAttempt.status == "failed",
            JobAttempt.created_at >= now - datetime.timedelta(hours=_ATTEMPT_LOOKBACK_HOURS),
            JobAttempt.job_id.in_(job_ids),
        )
        .distinct()
    )
    return set(result.scalars().all())


async def _expire_previous_attempt_if_needed(db: AsyncSession, job: Job, *, now: datetime.datetime) -> None:
    if job.status != "leased" or not job.lease_token or not job.node_id or not job.leased_until or job.leased_until >= now:
        return
    result = await db.execute(
        select(JobAttempt).where(
            JobAttempt.tenant_id == job.tenant_id,
            JobAttempt.job_id == job.job_id,
            JobAttempt.attempt_no == job.attempt,
            JobAttempt.lease_token == job.lease_token,
        )
    )
    attempt = result.scalars().first()
    await LeaseService.expire_previous_attempt_if_needed(db, job=job, now=now, attempt=attempt)


async def _create_attempt(
    db: AsyncSession,
    *,
    job: Job,
    node_id: str,
    score: int,
    now: datetime.datetime,
) -> JobAttempt:
    attempt = JobAttempt(
        tenant_id=job.tenant_id,
        attempt_id=str(uuid.uuid4()),
        job_id=job.job_id,
        node_id=node_id,
        lease_token=job.lease_token or "",
        attempt_no=job.attempt,
        status="leased",
        score=score,
        created_at=now,
        started_at=now,
        updated_at=now,
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def _get_attempt_for_callback(db: AsyncSession, job: Job, payload: JobLeaseAckRequest) -> JobAttempt | None:
    result = await db.execute(
        select(JobAttempt).where(
            JobAttempt.tenant_id == job.tenant_id,
            JobAttempt.job_id == job.job_id,
            JobAttempt.node_id == payload.node_id,
            JobAttempt.attempt_no == payload.attempt,
            JobAttempt.lease_token == payload.lease_token,
        )
    )
    return result.scalars().first()


async def _get_current_attempt(db: AsyncSession, job: Job) -> JobAttempt | None:
    if not job.lease_token or job.attempt <= 0:
        return None
    result = await db.execute(
        select(JobAttempt).where(
            JobAttempt.tenant_id == job.tenant_id,
            JobAttempt.job_id == job.job_id,
            JobAttempt.attempt_no == job.attempt,
            JobAttempt.lease_token == job.lease_token,
        )
    )
    return result.scalars().first()


async def _get_job_by_id(db: AsyncSession, tenant_id: str, job_id: str) -> Job:
    result = await db.execute(select(Job).where(Job.tenant_id == tenant_id, Job.job_id == job_id))
    job = result.scalars().first()
    if job is None:
        raise zen("ZEN-JOB-4040", "job not found", status_code=404)
    return job


async def _get_job_by_id_for_update(
    db: AsyncSession,
    tenant_id: str,
    job_id: str,
    *,
    skip_locked: bool = False,
) -> Job:
    """Get job by ID with row-level lock for safe updates.

    This function acquires a row-level lock to prevent race conditions between
    concurrent operations (e.g., lease expiration vs. job completion).

    Args:
        db: Database session
        tenant_id: Tenant ID
        job_id: Job ID
        skip_locked: If True, skip locked rows (for pull_jobs)

    Returns:
        Job instance with exclusive lock

    Raises:
        zen("ZEN-JOB-4040"): Job not found
    """
    stmt = select(Job).where(Job.tenant_id == tenant_id, Job.job_id == job_id).with_for_update(skip_locked=skip_locked)
    result = await db.execute(stmt)
    job = result.scalars().first()
    if job is None:
        raise zen("ZEN-JOB-4040", "job not found", status_code=404)
    return job
