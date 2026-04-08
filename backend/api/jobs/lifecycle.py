"""
ZEN70 Jobs API - lifecycle route assembly.

This module is the HTTP/control-plane entrypoint for lifecycle operations,
while the job state-machine workflow lives in the dedicated lifecycle service.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin, get_machine_tenant_db, get_node_machine_token, get_redis, get_tenant_db
from backend.kernel.contracts.errors import zen
from backend.kernel.scheduling.failure_control_plane import get_failure_control_plane
from backend.platform.redis.client import RedisClient

from .helpers import _utcnow
from .lifecycle_service import (
    build_default_job_lifecycle_dependencies,
    cancel_job_by_operator,
    complete_job_callback,
    fail_job_callback,
    renew_job_lease_callback,
    report_job_progress_callback,
    retry_job_by_operator,
)
from .models import JobActionRequest, JobFailRequest, JobLeaseResponse, JobProgressRequest, JobRenewRequest, JobResponse, JobResultRequest

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post("/{id}/result", response_model=JobResponse)
async def complete_job(
    id: str,
    payload: JobResultRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    return await complete_job_callback(
        id,
        payload,
        db=db,
        redis=redis,
        node_token=node_token,
        deps=build_default_job_lifecycle_dependencies(),
    )


@router.post("/{id}/fail", response_model=JobResponse)
async def fail_job(
    id: str,
    payload: JobFailRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    return await fail_job_callback(
        id,
        payload,
        db=db,
        redis=redis,
        node_token=node_token,
        deps=build_default_job_lifecycle_dependencies(),
    )


@router.post("/{id}/progress", response_model=JobResponse)
async def report_job_progress(
    id: str,
    payload: JobProgressRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobResponse:
    return await report_job_progress_callback(
        id,
        payload,
        db=db,
        redis=redis,
        node_token=node_token,
        deps=build_default_job_lifecycle_dependencies(),
    )


@router.post("/{id}/renew", response_model=JobLeaseResponse)
async def renew_job_lease(
    id: str,
    payload: JobRenewRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> JobLeaseResponse:
    return await renew_job_lease_callback(
        id,
        payload,
        db=db,
        redis=redis,
        node_token=node_token,
        deps=build_default_job_lifecycle_dependencies(),
    )


@router.post("/{id}/cancel", response_model=JobResponse)
async def cancel_job(
    id: str,
    payload: JobActionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    return await cancel_job_by_operator(
        id,
        payload,
        current_user=current_user,
        db=db,
        redis=redis,
        deps=build_default_job_lifecycle_dependencies(),
    )


@router.post("/{id}/retry", response_model=JobResponse)
async def retry_job_now(
    id: str,
    payload: JobActionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    return await retry_job_by_operator(
        id,
        payload,
        current_user=current_user,
        db=db,
        redis=redis,
        deps=build_default_job_lifecycle_dependencies(),
    )


@router.post("/control-plane/release-quarantine/{node_id}")
async def release_quarantine(
    node_id: str,
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    del current_user
    failure_control_plane = get_failure_control_plane()
    released = await failure_control_plane.release_quarantine(node_id)
    if not released:
        raise zen(
            "ZEN-FCP-4041",
            f"Node {node_id} is not currently quarantined",
            status_code=404,
            recovery_hint="Check the control plane snapshot for quarantined nodes",
        )
    return {"node_id": node_id, "quarantine": "released"}


@router.get("/control-plane/snapshot")
async def control_plane_snapshot(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    del current_user
    return await get_failure_control_plane().snapshot(now=_utcnow())


@router.get("/control-plane/governance/timeline")
async def governance_timeline(
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    since_hours: float | None = None,
    limit: int = 200,
    current_user: dict[str, object] = Depends(get_current_admin),
) -> list[dict[str, object]]:
    del current_user
    failure_control_plane = get_failure_control_plane()
    since = None
    if since_hours is not None:
        since = _utcnow() - datetime.timedelta(hours=since_hours)
    return await failure_control_plane.governance_timeline(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        since=since,
        limit=min(limit, 1000),
    )


@router.get("/control-plane/governance/stats")
async def governance_stats(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    del current_user
    return await get_failure_control_plane().governance_stats(now=_utcnow())


@router.get("/control-plane/fair-share/config")
async def fair_share_config(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> dict[str, object]:
    del current_user
    from backend.kernel.scheduling.queue_stratification import SERVICE_CLASS_CONFIG, get_fair_scheduler

    fair_scheduler = get_fair_scheduler()
    quotas = fair_scheduler.get_all_quotas()
    return {
        "service_classes": SERVICE_CLASS_CONFIG,
        "default_service_class": fair_scheduler._default_service_class,
        "tenant_quotas": {
            tid: {
                "max_jobs_per_round": quota.max_jobs_per_round,
                "weight": quota.weight,
                "service_class": quota.service_class,
            }
            for tid, quota in quotas.items()
        },
    }
