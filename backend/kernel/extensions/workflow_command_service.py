from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.kernel.contracts.errors import zen
from backend.models.workflow import Workflow, WorkflowStep

_WORKFLOW_ALLOWED_STEP_KEYS = frozenset({"id", "kind", "payload", "depends_on", "priority", "max_retries"})


class WorkflowCommandService:
    @staticmethod
    def validate_dag(steps: list[dict]) -> None:
        ids = {s["id"] for s in steps}
        if len(ids) != len(steps):
            raise ValueError("Duplicate step IDs in workflow")
        for step in steps:
            for dep in step.get("depends_on", []):
                if dep not in ids:
                    raise ValueError(f"Step '{step['id']}' depends on unknown step '{dep}'")
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

    @staticmethod
    def normalize_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from backend.kernel.extensions.job_kind_registry import validate_job_payload

        if not steps:
            raise ValueError("Workflow must contain at least one step")
        normalized_steps: list[dict[str, Any]] = []
        for index, raw_step in enumerate(steps, start=1):
            if not isinstance(raw_step, dict):
                raise ValueError(f"Workflow step #{index} must be an object")
            extra_keys = sorted(set(raw_step) - _WORKFLOW_ALLOWED_STEP_KEYS)
            raw_id = raw_step.get("id")
            step_label = str(raw_id or f"#{index}")
            if extra_keys:
                raise ValueError(f"Workflow step '{step_label}' contains unsupported fields: {', '.join(extra_keys)}")
            step_id = raw_step.get("id")
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(f"Workflow step #{index} must declare a non-empty id")
            kind = raw_step.get("kind")
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError(f"Workflow step '{step_id}' must declare a non-empty kind")
            payload = raw_step.get("payload", {}) or {}
            if not isinstance(payload, dict):
                raise ValueError(f"Workflow step '{step_id}' payload must be an object")
            depends_on = raw_step.get("depends_on", []) or []
            if not isinstance(depends_on, list) or any(not isinstance(dep, str) or not dep.strip() for dep in depends_on):
                raise ValueError(f"Workflow step '{step_id}' depends_on must contain non-empty step IDs")
            priority = raw_step.get("priority", 60)
            max_retries = raw_step.get("max_retries", 1)
            if not isinstance(priority, int) or isinstance(priority, bool):
                raise ValueError(f"Workflow step '{step_id}' priority must be an integer")
            if not isinstance(max_retries, int) or isinstance(max_retries, bool):
                raise ValueError(f"Workflow step '{step_id}' max_retries must be an integer")
            normalized_steps.append(
                {
                    "id": step_id.strip(),
                    "kind": kind.strip(),
                    "payload": validate_job_payload(kind.strip(), dict(payload)),
                    "depends_on": [dep.strip() for dep in depends_on],
                    "priority": priority,
                    "max_retries": max_retries,
                }
            )
        WorkflowCommandService.validate_dag(normalized_steps)
        return normalized_steps

    @staticmethod
    async def create_workflow(
        db: AsyncSession,
        *,
        tenant_id: str,
        name: str,
        steps: list[dict],
        description: str | None = None,
        created_by: str | None = None,
    ) -> Workflow:
        try:
            normalized_steps = WorkflowCommandService.normalize_steps(steps)
        except ValueError as exc:
            raise zen("ZEN-WF-4000", str(exc), status_code=400) from exc
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        workflow = Workflow(
            tenant_id=tenant_id,
            workflow_id=uuid.uuid4().hex,
            name=name,
            description=description,
            steps=normalized_steps,
            status="running",
            context={},
            created_by=created_by,
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        db.add(workflow)
        await db.flush()
        for step in normalized_steps:
            db.add(WorkflowStep(workflow_id_fk=workflow.id, step_id=step["id"], status="waiting"))
        await db.flush()
        await WorkflowCommandService.advance_workflow(db, workflow)
        return workflow

    @staticmethod
    async def advance_workflow(db: AsyncSession, workflow: Workflow) -> None:
        result = await db.execute(select(WorkflowStep).where(WorkflowStep.workflow_id_fk == workflow.id))
        step_records = {ws.step_id: ws for ws in result.scalars().all()}
        completed_ids = {sid for sid, ws in step_records.items() if ws.status == "completed"}
        failed_ids = {sid for sid, ws in step_records.items() if ws.status == "failed"}
        if failed_ids:
            workflow.status = "failed"
            workflow.completed_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            await db.flush()
            return
        if all(ws.status in ("completed", "skipped") for ws in step_records.values()):
            workflow.status = "completed"
            workflow.completed_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            await db.flush()
            return
        step_map = {s["id"]: s for s in workflow.steps}
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        for step_id, ws in step_records.items():
            if ws.status != "waiting":
                continue
            step_def = step_map[step_id]
            deps = step_def.get("depends_on", [])
            if all(dep in completed_ids for dep in deps):
                payload = dict(step_def.get("payload", {}))
                payload["_workflow_id"] = workflow.workflow_id
                payload["_step_id"] = step_id
                payload["_context"] = {dep: step_records[dep].result for dep in deps if step_records[dep].result}
                from backend.api.jobs.models import JobCreateRequest
                from backend.api.jobs.submission_service import submit_job

                submitted = await submit_job(
                    JobCreateRequest(
                        kind=step_def["kind"],
                        source="workflow-engine",
                        priority=step_def.get("priority", 60),
                        max_retries=step_def.get("max_retries", 1),
                        payload=payload,
                    ),
                    current_user={
                        "tenant_id": workflow.tenant_id,
                        "sub": workflow.created_by or "workflow-engine",
                        "username": workflow.created_by or "workflow-engine",
                    },
                    db=db,
                    redis=None,
                )
                ws.status = "running"
                ws.job_id = submitted.job_id
                ws.started_at = now
        workflow.updated_at = now
        await db.flush()

    @staticmethod
    async def mark_step_completed(
        db: AsyncSession,
        *,
        workflow_id: str,
        step_id: str,
        result_payload: dict[str, Any],
    ) -> None:
        wf_result = await db.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))
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
        ws.result = result_payload
        ws.completed_at = now
        ctx = dict(workflow.context or {})
        ctx[step_id] = result_payload
        workflow.context = ctx
        await db.flush()
        await WorkflowCommandService.advance_workflow(db, workflow)

    @staticmethod
    async def mark_step_failed(
        db: AsyncSession,
        *,
        workflow_id: str,
        step_id: str,
        error_message: str,
    ) -> None:
        wf_result = await db.execute(select(Workflow).where(Workflow.workflow_id == workflow_id))
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
        ws.status = "failed"
        ws.error_message = error_message
        ws.completed_at = now
        await db.flush()
        await WorkflowCommandService.advance_workflow(db, workflow)
