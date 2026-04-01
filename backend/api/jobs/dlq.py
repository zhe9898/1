from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import get_current_admin, get_redis, get_tenant_db
from backend.core.errors import zen
from backend.core.redis_client import CHANNEL_JOB_EVENTS, RedisClient
from backend.models.job import Job

from .database import _append_log, _get_job_by_id, _get_job_by_id_for_update, remove_from_dead_letter_queue
from .helpers import _to_response, _utcnow
from .models import DeadLetterQueueResponse, JobActionRequest, JobRequeueRequest, JobResponse

router = APIRouter()


@router.get("/dead-letter", response_model=DeadLetterQueueResponse)
async def list_dead_letter_queue(
    limit: int = 50,
    offset: int = 0,
    failure_category: str | None = None,
    connector_id: str | None = None,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> DeadLetterQueueResponse:
    """List jobs in dead-letter queue with pagination and filtering."""
    tenant_id = str(current_user.get("tenant_id") or "default")

    # Limit max results to prevent abuse
    limit = min(limit, 500)

    if redis:
        # Fast path: use Redis sorted set for pagination
        dlq_key = f"dlq:{tenant_id}:jobs"
        try:
            # Get total count
            total = await redis.zcard(dlq_key)  # type: ignore[attr-defined]

            # Get paginated job IDs (sorted by timestamp, newest first)
            job_ids = await redis.zrevrange(dlq_key, offset, offset + limit - 1)  # type: ignore[attr-defined]

            if not job_ids:
                return DeadLetterQueueResponse(total=total, items=[])

            # Fetch job details from PostgreSQL
            stmt = select(Job).where(
                Job.tenant_id == tenant_id,
                Job.job_id.in_(job_ids),
                Job.status == "failed",
            )

            if failure_category:
                stmt = stmt.where(Job.failure_category == failure_category)
            if connector_id:
                stmt = stmt.where(Job.connector_id == connector_id)

            stmt = stmt.order_by(Job.completed_at.desc())

            result = await db.execute(stmt)
            jobs = list(result.scalars().all())

            now = _utcnow()
            return DeadLetterQueueResponse(
                total=total,
                items=[_to_response(job, now=now) for job in jobs],
            )
        except (OSError, ValueError, KeyError, RuntimeError, TypeError):
            # Redis query failed, fall back to PostgreSQL-only path
            pass

    # Fallback: PostgreSQL-only query
    stmt = select(Job).where(
        Job.tenant_id == tenant_id,
        Job.status == "failed",
    )

    if failure_category:
        stmt = stmt.where(Job.failure_category == failure_category)
    if connector_id:
        stmt = stmt.where(Job.connector_id == connector_id)

    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Get paginated results
    stmt = stmt.order_by(Job.completed_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    jobs = list(result.scalars().all())

    now = _utcnow()
    return DeadLetterQueueResponse(
        total=total,
        items=[_to_response(job, now=now) for job in jobs],
    )


@router.post("/{id}/requeue", response_model=JobResponse)
async def requeue_job_from_dead_letter(
    id: str,
    payload: JobRequeueRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    """Requeue a failed job from dead-letter queue."""
    tenant_id = str(current_user.get("tenant_id") or "default")

    # Get job with row-level lock
    job = await _get_job_by_id_for_update(db, tenant_id, id)

    # Verify job is in failed state
    if job.status != "failed":
        raise zen(
            "ZEN-JOB-4091",
            "Job is not in failed state",
            status_code=409,
            recovery_hint="Only failed jobs can be requeued from DLQ",
            details={"job_id": id, "status": job.status},
        )

    # Remove from DLQ
    if redis:
        removed = await remove_from_dead_letter_queue(redis, tenant_id, id)
        if not removed:
            # Job not in Redis DLQ, but that's okay (degraded mode)
            pass

    # Reset job state
    now = _utcnow()
    if payload.reset_retry_count:
        job.retry_count = 0
    if payload.increase_max_retries is not None:
        job.max_retries = job.max_retries + payload.increase_max_retries

    job.status = "pending"
    job.node_id = None
    job.lease_token = None
    job.leased_until = None
    job.started_at = None
    job.completed_at = None
    job.error_message = None
    job.failure_category = None
    job.updated_at = now

    await db.flush()

    # Log requeue
    await _append_log(
        db,
        job.job_id,
        (
            f"job requeued from DLQ by {current_user.get('username', 'admin')} "
            f"reason={payload.reason} reset_retry={payload.reset_retry_count} "
            f"increase_max_retries={payload.increase_max_retries}"
        ),
        level="info",
        tenant_id=job.tenant_id,
    )

    response = _to_response(job, now=now)
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "requeued",
        {"job": response.model_dump(mode="json"), "source": "dlq", "reason": payload.reason},
    )
    await db.commit()

    return response


@router.delete("/{id}/dead-letter")
async def remove_job_from_dead_letter(
    id: str,
    payload: JobActionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> dict[str, object]:
    """Remove a job from dead-letter queue without requeuing."""
    tenant_id = str(current_user.get("tenant_id") or "default")

    # Verify job exists and is failed
    job = await _get_job_by_id(db, tenant_id, id)
    if job.status != "failed":
        raise zen(
            "ZEN-JOB-4091",
            "Job is not in failed state",
            status_code=409,
            recovery_hint="Only failed jobs can be removed from DLQ",
            details={"job_id": id, "status": job.status},
        )

    # Remove from Redis DLQ
    if redis:
        await remove_from_dead_letter_queue(redis, tenant_id, id)

    # Log removal
    await _append_log(
        db,
        job.job_id,
        f"job removed from DLQ by {current_user.get('username', 'admin')} reason={payload.reason or 'manual removal'}",
        level="info",
        tenant_id=job.tenant_id,
    )
    await db.commit()

    return {
        "job_id": id,
        "removed_at": _utcnow().isoformat(),
        "reason": payload.reason or "manual removal",
    }
