"""Workflow tenant/query helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.models.job import Job
from backend.models.workflow import Workflow, WorkflowStep


def workflow_stmt_for_tenant(tenant_id: str) -> Select[tuple[Workflow]]:
    return select(Workflow).where(Workflow.tenant_id == tenant_id)


async def load_workflow_for_tenant(
    db: AsyncSession,
    *,
    tenant_id: str,
    workflow_id: str,
) -> Workflow | None:
    result = await db.execute(workflow_stmt_for_tenant(tenant_id).where(Workflow.workflow_id == workflow_id))
    return result.scalars().first()


async def list_workflow_steps(
    db: AsyncSession,
    *,
    workflow_id_fk: int,
) -> list[WorkflowStep]:
    result = await db.execute(select(WorkflowStep).where(WorkflowStep.workflow_id_fk == workflow_id_fk))
    return list(result.scalars().all())


async def load_workflow_step(
    db: AsyncSession,
    *,
    workflow_id_fk: int,
    step_id: str,
) -> WorkflowStep | None:
    result = await db.execute(
        select(WorkflowStep).where(
            WorkflowStep.workflow_id_fk == workflow_id_fk,
            WorkflowStep.step_id == step_id,
        )
    )
    return result.scalars().first()


async def load_job_for_tenant(
    db: AsyncSession,
    *,
    tenant_id: str,
    job_id: str,
) -> Job | None:
    result = await db.execute(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.job_id == job_id,
        )
    )
    return result.scalars().first()
