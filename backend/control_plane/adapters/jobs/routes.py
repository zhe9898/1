"""
ZEN70 Jobs API - CRUD endpoints and job schema.

Dispatch (pull/explain) lives in dispatch.py; lifecycle callbacks live in
lifecycle.py. Job submission is delegated to submission.py so other control-
plane surfaces can reuse the exact same admission, idempotency, and event
contracts.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_user, get_redis, get_tenant_db, require_scope
from backend.control_plane.adapters.ui_contracts import ResourceSchemaResponse
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.platform.redis.client import RedisClient

from .database import _get_job_by_id
from .helpers import _matches_job_list_filters, _normalize_job_status_filter, _to_attempt_response, _to_response, _utcnow
from .models import JobAttemptResponse, JobCreateRequest, JobResponse
from .schemas import _resource_schema
from .submission_service import submit_job

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
_WRITE_JOBS_SCOPE_DEPENDENCY = require_scope("write:jobs")


@router.get("/schema", response_model=ResourceSchemaResponse)
async def get_job_schema(
    current_user: dict[str, object] = Depends(get_current_user),
) -> ResourceSchemaResponse:
    del current_user
    return _resource_schema()


@router.post("", response_model=JobResponse)
async def create_job(
    payload: JobCreateRequest,
    current_user: dict[str, object] = Depends(_WRITE_JOBS_SCOPE_DEPENDENCY),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> JobResponse:
    return await submit_job(payload, current_user=current_user, db=db, redis=redis)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    job_id: str | None = None,
    status: str | None = None,
    lease_state: str | None = None,
    priority_bucket: str | None = None,
    target_executor: str | None = None,
    target_zone: str | None = None,
    required_capability: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[JobResponse]:
    tenant_id = require_current_user_tenant_id(current_user)
    status_filter = _normalize_job_status_filter(status) if status else None
    query = select(Job).where(Job.tenant_id == tenant_id)
    if job_id:
        query = query.where(Job.job_id == job_id)
    if target_executor:
        query = query.where(Job.target_executor == target_executor)
    if target_zone:
        query = query.where(Job.target_zone == target_zone)
    result = await db.execute(query.order_by(Job.priority.desc(), Job.created_at.desc()).limit(limit).offset(offset))
    now = _utcnow()
    jobs = [
        job
        for job in result.scalars().all()
        if _matches_job_list_filters(
            job,
            now=now,
            status=status_filter,
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
    tenant_id = require_current_user_tenant_id(current_user)
    now = _utcnow()
    job = await _get_job_by_id(db, tenant_id, id)
    return _to_response(job, now=now)


@router.get("/{id}/attempts", response_model=list[JobAttemptResponse])
async def list_job_attempts(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[JobAttemptResponse]:
    tenant_id = require_current_user_tenant_id(current_user)
    result = await db.execute(
        select(JobAttempt)
        .where(JobAttempt.tenant_id == tenant_id, JobAttempt.job_id == id)
        .order_by(JobAttempt.attempt_no.desc(), JobAttempt.created_at.desc())
    )
    return [_to_attempt_response(attempt) for attempt in result.scalars().all()]


# ============================================================================
# Dead-Letter Queue API Endpoints
# ============================================================================
