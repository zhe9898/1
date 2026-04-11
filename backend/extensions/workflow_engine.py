"""Workflow DAG engine.

Gateway responsibilities (this module):
  - Validate DAG (no cycles, valid step references)
  - Determine which steps are ready to run (dependencies met)
  - Dispatch Jobs for ready steps
  - Advance workflow state as Jobs complete

Runner responsibilities (via Jobs):
  - Execute each step's actual work
  - Report results back via complete_job / fail_job
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.extensions.workflow_command_service import WorkflowCommandService
from backend.models.workflow import Workflow

# ── DAG Validation ────────────────────────────────────────────────────────


def validate_dag(steps: list[dict]) -> None:
    WorkflowCommandService.validate_dag(steps)


def normalize_workflow_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return WorkflowCommandService.normalize_steps(steps)


def topological_order(steps: list[dict]) -> list[str]:
    """Return step IDs in topological order (dependencies first)."""
    adj: dict[str, list[str]] = {s["id"]: s.get("depends_on", []) for s in steps}
    order: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        for dep in adj.get(node, []):
            visit(dep)
        visited.add(node)
        order.append(node)

    for step_id in adj:
        visit(step_id)
    return order


# ── Workflow Lifecycle ────────────────────────────────────────────────────


async def create_workflow(
    db: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    steps: list[dict],
    description: str | None = None,
    created_by: str | None = None,
) -> Workflow:
    return await WorkflowCommandService.create_workflow(
        db,
        tenant_id=tenant_id,
        name=name,
        steps=steps,
        description=description,
        created_by=created_by,
    )


async def _advance_workflow(
    db: AsyncSession,
    workflow: Workflow,
    steps: list[dict],
) -> None:
    del steps
    await WorkflowCommandService.advance_workflow(db, workflow)


async def on_step_job_completed(
    db: AsyncSession,
    workflow_id: str,
    step_id: str,
    result: dict[str, Any],
) -> None:
    await WorkflowCommandService.mark_step_completed(
        db,
        workflow_id=workflow_id,
        step_id=step_id,
        result_payload=result,
    )


async def on_step_job_failed(
    db: AsyncSession,
    workflow_id: str,
    step_id: str,
    error_message: str,
) -> None:
    await WorkflowCommandService.mark_step_failed(
        db,
        workflow_id=workflow_id,
        step_id=step_id,
        error_message=error_message,
    )
