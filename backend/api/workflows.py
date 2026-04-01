"""Workflow API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_tenant_db
from backend.core.errors import zen
from backend.core.workflow_engine import create_workflow, on_step_job_completed, on_step_job_failed
from backend.models.workflow import Workflow, WorkflowStep

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


# ── Request / Response models ──────────────────────────────────────────────


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None)
    steps: list[dict] = Field(..., min_length=1)


class StepStatus(BaseModel):
    step_id: str
    job_id: str | None
    status: str
    result: dict | None
    error_message: str | None
    started_at: str | None
    completed_at: str | None


class WorkflowResponse(BaseModel):
    workflow_id: str
    name: str
    description: str | None
    status: str
    steps_count: int
    created_by: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


class WorkflowDetailResponse(WorkflowResponse):
    steps_definition: list[dict]
    steps_status: list[StepStatus]
    context: dict


def _to_response(wf: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        workflow_id=wf.workflow_id,
        name=wf.name,
        description=wf.description,
        status=wf.status,
        steps_count=len(wf.steps),
        created_by=wf.created_by,
        created_at=wf.created_at.isoformat(),
        started_at=wf.started_at.isoformat() if wf.started_at else None,
        completed_at=wf.completed_at.isoformat() if wf.completed_at else None,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post("", response_model=WorkflowDetailResponse)
async def start_workflow(
    payload: WorkflowCreateRequest,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowDetailResponse:
    """Start a new workflow. Dispatches Jobs for ready steps immediately."""
    workflow = await create_workflow(
        db,
        tenant_id=current_user["tenant_id"],
        name=payload.name,
        description=payload.description,
        steps=payload.steps,
        created_by=current_user["username"],
    )

    steps_result = await db.execute(select(WorkflowStep).where(WorkflowStep.workflow_id_fk == workflow.id))
    steps_status = [
        StepStatus(
            step_id=ws.step_id,
            job_id=ws.job_id,
            status=ws.status,
            result=ws.result,
            error_message=ws.error_message,
            started_at=ws.started_at.isoformat() if ws.started_at else None,
            completed_at=ws.completed_at.isoformat() if ws.completed_at else None,
        )
        for ws in steps_result.scalars().all()
    ]

    return WorkflowDetailResponse(
        **_to_response(workflow).model_dump(),
        steps_definition=workflow.steps,
        steps_status=steps_status,
        context=workflow.context,
    )


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[WorkflowResponse]:
    """List workflows for this tenant (newest first)."""
    query = select(Workflow).where(Workflow.tenant_id == current_user["tenant_id"])
    if status:
        query = query.where(Workflow.status == status)
    result = await db.execute(query.order_by(desc(Workflow.created_at)).limit(limit))
    return [_to_response(wf) for wf in result.scalars().all()]


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)
async def get_workflow(
    workflow_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowDetailResponse:
    """Get workflow detail including step statuses."""
    result = await db.execute(
        select(Workflow).where(
            Workflow.workflow_id == workflow_id,
            Workflow.tenant_id == current_user["tenant_id"],
        )
    )
    workflow = result.scalars().first()
    if workflow is None:
        raise zen("ZEN-WF-4040", "Workflow not found", status_code=404)

    steps_result = await db.execute(select(WorkflowStep).where(WorkflowStep.workflow_id_fk == workflow.id))
    steps_status = [
        StepStatus(
            step_id=ws.step_id,
            job_id=ws.job_id,
            status=ws.status,
            result=ws.result,
            error_message=ws.error_message,
            started_at=ws.started_at.isoformat() if ws.started_at else None,
            completed_at=ws.completed_at.isoformat() if ws.completed_at else None,
        )
        for ws in steps_result.scalars().all()
    ]

    return WorkflowDetailResponse(
        **_to_response(workflow).model_dump(),
        steps_definition=workflow.steps,
        steps_status=steps_status,
        context=workflow.context,
    )


@router.post("/{workflow_id}/steps/{step_id}/complete")
async def report_step_complete(
    workflow_id: str,
    step_id: str,
    result: dict,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Report a workflow step as completed (called by runner-agent callback)."""
    await on_step_job_completed(db, workflow_id, step_id, result)
    return {"status": "ok"}


@router.post("/{workflow_id}/steps/{step_id}/fail")
async def report_step_failed(
    workflow_id: str,
    step_id: str,
    error: str,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Report a workflow step as failed."""
    await on_step_job_failed(db, workflow_id, step_id, error)
    return {"status": "ok"}
