"""Workflow API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_user, get_machine_tenant_db, get_node_machine_token, get_tenant_db
from backend.extensions.workflow_engine import create_workflow, on_step_job_completed, on_step_job_failed
from backend.kernel.contracts.errors import zen
from backend.kernel.contracts.status import canonicalize_status
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.models.workflow import Workflow

from .workflow_contracts import (  # noqa: F401 re-export
    StepStatus,
    WorkflowCreateRequest,
    WorkflowDetailResponse,
    WorkflowResponse,
    WorkflowStepCompleteRequest,
    WorkflowStepDefinition,
    WorkflowStepFailRequest,
)
from .workflow_machine_callbacks import assert_machine_step_callback_contract
from .workflow_projection import build_workflow_detail_response, workflow_to_response
from .workflow_queries import list_workflow_steps, load_workflow_for_tenant, workflow_stmt_for_tenant

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowDetailResponse)
async def start_workflow(
    payload: WorkflowCreateRequest,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowDetailResponse:
    """Start a new workflow. Dispatches Jobs for ready steps immediately."""
    tenant_id = require_current_user_tenant_id(current_user)
    workflow = await create_workflow(
        db,
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        steps=[step.model_dump(mode="python") for step in payload.steps],
        created_by=current_user["username"],
    )
    return build_workflow_detail_response(
        workflow,
        await list_workflow_steps(db, workflow_id_fk=workflow.id),
    )


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[WorkflowResponse]:
    """List workflows for this tenant (newest first)."""
    tenant_id = require_current_user_tenant_id(current_user)
    query = workflow_stmt_for_tenant(tenant_id)
    if status:
        query = query.where(Workflow.status == canonicalize_status("workflows.status", status))
    result = await db.execute(query.order_by(desc(Workflow.created_at)).limit(limit))
    return [workflow_to_response(workflow) for workflow in result.scalars().all()]


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)
async def get_workflow(
    workflow_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowDetailResponse:
    """Get workflow detail including step statuses."""
    tenant_id = require_current_user_tenant_id(current_user)
    workflow = await load_workflow_for_tenant(
        db,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
    )
    if workflow is None:
        raise zen("ZEN-WF-4040", "Workflow not found", status_code=404)
    return build_workflow_detail_response(
        workflow,
        await list_workflow_steps(db, workflow_id_fk=workflow.id),
    )


@router.post("/{workflow_id}/steps/{step_id}/complete")
async def report_step_complete(
    workflow_id: str,
    step_id: str,
    payload: WorkflowStepCompleteRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    node_token: str = Depends(get_node_machine_token),
) -> dict[str, str]:
    """Report a workflow step as completed (machine callback only)."""
    await assert_machine_step_callback_contract(
        db,
        workflow_id=workflow_id,
        step_id=step_id,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        job_id=payload.job_id,
        lease_token=payload.lease_token,
        attempt=payload.attempt,
        node_token=node_token,
    )
    await on_step_job_completed(db, workflow_id, step_id, payload.result)
    return {"status": "ok"}


@router.post("/{workflow_id}/steps/{step_id}/fail")
async def report_step_failed(
    workflow_id: str,
    step_id: str,
    payload: WorkflowStepFailRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    node_token: str = Depends(get_node_machine_token),
) -> dict[str, str]:
    """Report a workflow step as failed (machine callback only)."""
    await assert_machine_step_callback_contract(
        db,
        workflow_id=workflow_id,
        step_id=step_id,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        job_id=payload.job_id,
        lease_token=payload.lease_token,
        attempt=payload.attempt,
        node_token=node_token,
    )
    await on_step_job_failed(db, workflow_id, step_id, payload.error)
    return {"status": "ok"}
