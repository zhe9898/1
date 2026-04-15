"""Workflow response projection helpers."""

from __future__ import annotations

from backend.kernel.contracts.status import normalize_persisted_status
from backend.models.workflow import Workflow, WorkflowStep

from .workflow_contracts import StepStatus, WorkflowDetailResponse, WorkflowResponse


def workflow_to_response(workflow: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        workflow_id=workflow.workflow_id,
        name=workflow.name,
        description=workflow.description,
        status=normalize_persisted_status("workflows.status", workflow.status) or "pending",
        steps_count=len(workflow.steps or []),
        created_by=workflow.created_by,
        created_at=workflow.created_at.isoformat(),
        started_at=workflow.started_at.isoformat() if workflow.started_at else None,
        completed_at=workflow.completed_at.isoformat() if workflow.completed_at else None,
    )


def workflow_step_to_status(workflow_step: WorkflowStep) -> StepStatus:
    return StepStatus(
        step_id=workflow_step.step_id,
        job_id=workflow_step.job_id,
        status=normalize_persisted_status("workflow_steps.status", workflow_step.status) or "waiting",
        result=workflow_step.result,
        error_message=workflow_step.error_message,
        started_at=workflow_step.started_at.isoformat() if workflow_step.started_at else None,
        completed_at=workflow_step.completed_at.isoformat() if workflow_step.completed_at else None,
    )


def build_workflow_detail_response(
    workflow: Workflow,
    workflow_steps: list[WorkflowStep],
) -> WorkflowDetailResponse:
    return WorkflowDetailResponse(
        **workflow_to_response(workflow).model_dump(),
        steps_definition=list(workflow.steps or []),
        steps_status=[workflow_step_to_status(step) for step in workflow_steps],
        context=dict(workflow.context or {}),
    )
