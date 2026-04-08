from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import get_current_admin, get_current_user, get_redis, get_tenant_db
from backend.kernel.contracts.errors import zen
from backend.models.job import Job
from backend.platform.redis.client import CHANNEL_JOB_EVENTS, RedisClient

from .database import _append_log, _get_job_by_id_for_update
from .helpers import _utcnow
from .models import (
    ConcurrentLimitInfo,
    JobPriorityUpdateRequest,
    JobPriorityUpdateResponse,
    JobTypeStatsItem,
    JobTypeStatsResponse,
    QueueLayerStats,
    QueueStatsResponse,
)

router = APIRouter()


@router.get("/queue/stats", response_model=QueueStatsResponse)
async def get_queue_stats(
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> QueueStatsResponse:
    """Get queue statistics grouped by priority layer."""
    from backend.kernel.scheduling.queue_stratification import get_priority_layer_stats

    tenant_id = str(current_user.get("tenant_id") or "default")

    # Query pending jobs
    stmt = select(Job).where(
        Job.tenant_id == tenant_id,
        Job.status == "pending",
    )
    result = await db.execute(stmt)
    jobs = list(result.scalars().all())

    # Calculate stats
    layer_stats = get_priority_layer_stats(jobs)

    # Convert to response format
    by_priority = {
        layer: QueueLayerStats(
            count=stats["count"],
            oldest=stats["oldest"],
        )
        for layer, stats in layer_stats.items()
    }

    return QueueStatsResponse(
        by_priority=by_priority,
        total_pending=len(jobs),
    )


@router.post("/{id}/priority", response_model=JobPriorityUpdateResponse)
async def update_job_priority(
    id: str,
    payload: JobPriorityUpdateRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobPriorityUpdateResponse:
    """Update job priority (admin only)."""
    from backend.kernel.scheduling.queue_stratification import get_priority_layer

    tenant_id = str(current_user.get("tenant_id") or "default")

    # Get job with row-level lock
    job = await _get_job_by_id_for_update(db, tenant_id, id)

    # Only pending jobs can have priority updated
    if job.status != "pending":
        raise zen(
            "ZEN-JOB-4092",
            "Only pending jobs can have priority updated",
            status_code=409,
            recovery_hint="Job must be in pending state",
            details={"job_id": id, "status": job.status},
        )

    # Record old values
    old_priority = job.priority
    old_layer = get_priority_layer(old_priority)

    # Update priority
    job.priority = payload.priority
    new_layer = get_priority_layer(payload.priority)

    now = _utcnow()
    job.updated_at = now

    await db.flush()

    # Log priority change
    await _append_log(
        db,
        job.job_id,
        f"priority updated by {current_user.get('username', 'admin')} from {old_priority} to {payload.priority} ({old_layer} -> {new_layer}) "
        f"reason={payload.reason}",
        level="info",
        tenant_id=job.tenant_id,
    )
    await db.commit()

    # Publish event
    await publish_control_event(
        redis,
        CHANNEL_JOB_EVENTS,
        "priority_updated",
        {
            "job_id": job.job_id,
            "old_priority": old_priority,
            "new_priority": payload.priority,
            "old_layer": old_layer,
            "new_layer": new_layer,
            "reason": payload.reason,
        },
    )

    return JobPriorityUpdateResponse(
        job_id=job.job_id,
        old_priority=old_priority,
        new_priority=payload.priority,
        old_layer=old_layer,
        new_layer=new_layer,
        updated_at=now,
    )


# ============================================================================
# Job Type Separation API Endpoints
# ============================================================================


@router.get("/stats/by-type", response_model=JobTypeStatsResponse)
async def get_job_stats_by_type(
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobTypeStatsResponse:
    """Get job statistics grouped by type (scheduled vs background)."""
    from backend.kernel.execution.job_type_separation import (
        SCHEDULED_JOB_SOURCES,
        get_job_type_stats,
        get_max_concurrent_limit,
    )

    tenant_id = str(current_user.get("tenant_id") or "default")

    # Query all jobs for this tenant
    stmt = select(Job).where(Job.tenant_id == tenant_id)
    result = await db.execute(stmt)
    jobs = list(result.scalars().all())

    # Calculate stats
    stats = get_job_type_stats(jobs)

    # Get concurrent counts
    scheduled_concurrent_stmt = select(func.count()).where(
        Job.tenant_id == tenant_id,
        Job.status == "leased",
        Job.source.in_(list(SCHEDULED_JOB_SOURCES)),
    )
    scheduled_concurrent_result = await db.execute(scheduled_concurrent_stmt)
    scheduled_concurrent = scheduled_concurrent_result.scalar() or 0

    background_concurrent_stmt = select(func.count()).where(
        Job.tenant_id == tenant_id,
        Job.status == "leased",
        ~Job.source.in_(list(SCHEDULED_JOB_SOURCES)),
    )
    background_concurrent_result = await db.execute(background_concurrent_stmt)
    background_concurrent = background_concurrent_result.scalar() or 0

    return JobTypeStatsResponse(
        scheduled=JobTypeStatsItem(**stats["scheduled"]),
        background=JobTypeStatsItem(**stats["background"]),
        concurrent_limits={
            "scheduled": ConcurrentLimitInfo(
                current=scheduled_concurrent,
                max=get_max_concurrent_limit("scheduled", "per_tenant"),
            ),
            "background": ConcurrentLimitInfo(
                current=background_concurrent,
                max=get_max_concurrent_limit("background", "per_tenant"),
            ),
        },
    )
