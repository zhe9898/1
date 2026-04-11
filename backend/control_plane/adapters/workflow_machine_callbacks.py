"""Workflow machine-callback contract validation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.contracts.errors import zen
from backend.runtime.topology.node_auth import authenticate_node_request

from .workflow_queries import load_job_for_tenant, load_workflow_for_tenant, load_workflow_step


async def assert_machine_step_callback_contract(
    db: AsyncSession,
    *,
    workflow_id: str,
    step_id: str,
    tenant_id: str,
    node_id: str,
    job_id: str,
    lease_token: str,
    attempt: int,
    node_token: str,
) -> None:
    await authenticate_node_request(db, node_id, node_token, require_active=True, tenant_id=tenant_id)

    workflow = await load_workflow_for_tenant(
        db,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
    )
    if workflow is None:
        raise zen("ZEN-WF-4040", "Workflow not found", status_code=404)

    workflow_step = await load_workflow_step(
        db,
        workflow_id_fk=workflow.id,
        step_id=step_id,
    )
    if workflow_step is None:
        raise zen("ZEN-WF-4041", "Workflow step not found", status_code=404)
    if not workflow_step.job_id or workflow_step.job_id != job_id:
        raise zen(
            "ZEN-WF-4092",
            "Step callback job_id mismatch",
            status_code=409,
            recovery_hint="Report status using the workflow-assigned step job_id",
            details={"workflow_id": workflow_id, "step_id": step_id, "expected_job_id": workflow_step.job_id, "job_id": job_id},
        )

    job = await load_job_for_tenant(
        db,
        tenant_id=tenant_id,
        job_id=job_id,
    )
    if job is None:
        raise zen("ZEN-JOB-4040", "Job not found", status_code=404)
    if str(job.source or "") != "workflow-engine":
        raise zen(
            "ZEN-WF-4093",
            "Step callback is only allowed for workflow-engine jobs",
            status_code=409,
            details={"job_id": job_id, "source": job.source},
        )
    if str(job.node_id or "") != node_id:
        raise zen(
            "ZEN-JOB-4090",
            "Lease owner mismatch for workflow step callback",
            status_code=409,
            details={"job_id": job_id, "node_id": node_id, "job_node_id": job.node_id},
        )
    if str(job.lease_token or "") != lease_token:
        raise zen(
            "ZEN-JOB-4091",
            "Lease token mismatch for workflow step callback",
            status_code=409,
            details={"job_id": job_id},
        )
    if int(job.attempt or 0) != int(attempt):
        raise zen(
            "ZEN-JOB-4092",
            "Attempt mismatch for workflow step callback",
            status_code=409,
            details={"job_id": job_id, "expected_attempt": int(job.attempt or 0), "attempt": int(attempt)},
        )
