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

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import zen
from backend.models.job import Job
from backend.models.workflow import Workflow, WorkflowStep


# ── DAG Validation ────────────────────────────────────────────────────────

def validate_dag(steps: list[dict]) -> None:
    """Validate DAG structure: unique IDs, valid depends_on, no cycles.

    Raises ValueError with a descriptive message on any violation.
    """
    ids = {s["id"] for s in steps}
    if len(ids) != len(steps):
        raise ValueError("Duplicate step IDs in workflow")

    for step in steps:
        for dep in step.get("depends_on", []):
            if dep not in ids:
                raise ValueError(f"Step '{step['id']}' depends on unknown step '{dep}'")

    # Cycle detection via DFS
    adj: dict[str, list[str]] = {s["id"]: s.get("depends_on", []) for s in steps}
    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node: str) -> None:
        visited.add(node)
        in_stack.add(node)
        for dep in adj.get(node, []):
            if dep not in visited:
                dfs(dep)
            elif dep in in_stack:
                raise ValueError(f"Cycle detected involving step '{dep}'")
        in_stack.discard(node)

    for step_id in adj:
        if step_id not in visited:
            dfs(step_id)


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
    """Create and immediately start a workflow.

    Validates the DAG, persists the workflow, creates WorkflowStep
    records, and dispatches Jobs for all steps with no dependencies.
    """
    try:
        validate_dag(steps)
    except ValueError as e:
        raise zen("ZEN-WF-4000", str(e), status_code=400) from e

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    workflow = Workflow(
        tenant_id=tenant_id,
        workflow_id=uuid.uuid4().hex,
        name=name,
        description=description,
        steps=steps,
        status="running",
        context={},
        created_by=created_by,
        created_at=now,
        started_at=now,
        updated_at=now,
    )
    db.add(workflow)
    await db.flush()

    # Create step records
    for step in steps:
        ws = WorkflowStep(
            workflow_id_fk=workflow.id,
            step_id=step["id"],
            status="waiting",
        )
        db.add(ws)
    await db.flush()

    # Dispatch ready steps (no dependencies)
    await _advance_workflow(db, workflow, steps)
    return workflow


async def _advance_workflow(
    db: AsyncSession,
    workflow: Workflow,
    steps: list[dict],
) -> None:
    """Dispatch Jobs for all steps whose dependencies are completed."""
    # Load current step states
    result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.workflow_id_fk == workflow.id)
    )
    step_records = {ws.step_id: ws for ws in result.scalars().all()}

    completed_ids = {sid for sid, ws in step_records.items() if ws.status == "completed"}
    failed_ids = {sid for sid, ws in step_records.items() if ws.status == "failed"}

    # If any step failed, fail the whole workflow
    if failed_ids:
        workflow.status = "failed"
        workflow.completed_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        return

    # Check if all steps are done
    all_done = all(ws.status in ("completed", "skipped") for ws in step_records.values())
    if all_done:
        workflow.status = "completed"
        workflow.completed_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        return

    # Dispatch steps that are ready (all deps completed, not yet dispatched)
    step_map = {s["id"]: s for s in steps}
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    for step_id, ws in step_records.items():
        if ws.status != "waiting":
            continue
        step_def = step_map[step_id]
        deps = step_def.get("depends_on", [])
        if all(d in completed_ids for d in deps):
            # Inject outputs from completed dependencies into payload
            payload = dict(step_def.get("payload", {}))
            payload["_workflow_id"] = workflow.workflow_id
            payload["_step_id"] = step_id
            payload["_context"] = {
                dep: step_records[dep].result
                for dep in deps
                if step_records[dep].result
            }

            job = Job(
                tenant_id=workflow.tenant_id,
                job_id=uuid.uuid4().hex,
                kind=step_def["kind"],
                source="workflow-engine",
                status="pending",
                priority=step_def.get("priority", 60),
                max_retries=step_def.get("max_retries", 1),
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            db.add(job)
            await db.flush()

            ws.status = "pending"
            ws.job_id = job.job_id
            ws.started_at = now

    workflow.updated_at = now


async def on_step_job_completed(
    db: AsyncSession,
    workflow_id: str,
    step_id: str,
    result: dict[str, Any],
) -> None:
    """Called when a workflow step's Job completes successfully.

    Advances the workflow: marks step completed, dispatches next ready steps.
    """
    wf_result = await db.execute(
        select(Workflow).where(Workflow.workflow_id == workflow_id)
    )
    workflow = wf_result.scalars().first()
    if workflow is None:
        return

    ws_result = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.workflow_id_fk == workflow.id,
            WorkflowStep.step_id == step_id,
        )
    )
    ws = ws_result.scalars().first()
    if ws is None:
        return

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    ws.status = "completed"
    ws.result = result
    ws.completed_at = now

    # Update shared context
    ctx = dict(workflow.context or {})
    ctx[step_id] = result
    workflow.context = ctx

    await db.flush()
    await _advance_workflow(db, workflow, workflow.steps)


async def on_step_job_failed(
    db: AsyncSession,
    workflow_id: str,
    step_id: str,
    error_message: str,
) -> None:
    """Called when a workflow step's Job fails."""
    wf_result = await db.execute(
        select(Workflow).where(Workflow.workflow_id == workflow_id)
    )
    workflow = wf_result.scalars().first()
    if workflow is None:
        return

    ws_result = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.workflow_id_fk == workflow.id,
            WorkflowStep.step_id == step_id,
        )
    )
    ws = ws_result.scalars().first()
    if ws:
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        ws.status = "failed"
        ws.error_message = error_message
        ws.completed_at = now

    await db.flush()
    await _advance_workflow(db, workflow, workflow.steps)
