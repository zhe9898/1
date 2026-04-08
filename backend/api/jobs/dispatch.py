"""
ZEN70 Jobs API - dispatch route assembly.

This module is only the HTTP entrypoint for dispatch flows. All runtime
dependencies and execution logic live in dedicated service modules.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import (
    get_current_user,
    get_machine_tenant_db,
    get_node_machine_token,
    get_redis,
    get_tenant_db,
)
from backend.platform.redis.client import RedisClient

from .explain_service import (
    build_default_explain_job_dependencies,
    explain_job_details,
)
from .models import JobExplainResponse, JobLeaseResponse, JobPullRequest
from .pull_service import build_default_pull_jobs_dependencies, execute_pull_jobs

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post("/pull", response_model=list[JobLeaseResponse])
async def pull_jobs(
    payload: JobPullRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> list[JobLeaseResponse]:
    return await execute_pull_jobs(
        payload,
        db=db,
        redis=redis,
        node_token=node_token,
        deps=build_default_pull_jobs_dependencies(),
    )


@router.get("/{id}/explain", response_model=JobExplainResponse)
async def explain_job(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobExplainResponse:
    return await explain_job_details(
        id,
        current_user=current_user,
        db=db,
        deps=build_default_explain_job_dependencies(),
    )
