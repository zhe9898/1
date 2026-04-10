from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.control_plane.adapters.workflows import (
    WorkflowStepCompleteRequest,
    WorkflowStepFailRequest,
    report_step_complete,
    report_step_failed,
)


def _scalar_result(value: object | None) -> object:
    class _R:
        def __init__(self, val: object | None) -> None:
            self._val = val

        def scalars(self) -> object:
            class _S:
                def __init__(self, val: object | None) -> None:
                    self._val = val

                def first(self) -> object | None:
                    return self._val

            return _S(self._val)

    return _R(value)


@pytest.mark.asyncio
async def test_report_step_complete_rejects_job_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    workflow = SimpleNamespace(id=11, workflow_id="wf-1", tenant_id="tenant-a")
    step = SimpleNamespace(step_id="s1", job_id="job-expected")
    job = SimpleNamespace(job_id="job-actual", source="workflow-engine", node_id="node-a", lease_token="lease-aaa", attempt=1)
    db.execute.side_effect = [_scalar_result(workflow), _scalar_result(step), _scalar_result(job)]

    monkeypatch.setattr("backend.control_plane.adapters.workflows.authenticate_node_request", AsyncMock(return_value=SimpleNamespace(node_id="node-a")))

    with pytest.raises(HTTPException) as exc:
        await report_step_complete(
            "wf-1",
            "s1",
            WorkflowStepCompleteRequest(
                tenant_id="tenant-a",
                node_id="node-a",
                job_id="job-actual",
                lease_token="lease-aaa",
                attempt=1,
                result={"ok": True},
            ),
            db=db,
            node_token="token-a",
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_report_step_complete_accepts_valid_machine_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    workflow = SimpleNamespace(id=11, workflow_id="wf-1", tenant_id="tenant-a")
    step = SimpleNamespace(step_id="s1", job_id="job-1")
    job = SimpleNamespace(job_id="job-1", source="workflow-engine", node_id="node-a", lease_token="lease-aaa", attempt=2)
    db.execute.side_effect = [_scalar_result(workflow), _scalar_result(step), _scalar_result(job)]

    monkeypatch.setattr("backend.control_plane.adapters.workflows.authenticate_node_request", AsyncMock(return_value=SimpleNamespace(node_id="node-a")))
    on_complete = AsyncMock(return_value=None)
    monkeypatch.setattr("backend.control_plane.adapters.workflows.on_step_job_completed", on_complete)

    response = await report_step_complete(
        "wf-1",
        "s1",
        WorkflowStepCompleteRequest(
            tenant_id="tenant-a",
            node_id="node-a",
            job_id="job-1",
            lease_token="lease-aaa",
            attempt=2,
            result={"ok": True},
        ),
        db=db,
        node_token="token-a",
    )

    assert response == {"status": "ok"}
    on_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_step_failed_validates_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    workflow = SimpleNamespace(id=11, workflow_id="wf-1", tenant_id="tenant-a")
    step = SimpleNamespace(step_id="s1", job_id="job-1")
    job = SimpleNamespace(job_id="job-1", source="workflow-engine", node_id="node-a", lease_token="lease-aaa", attempt=3)
    db.execute.side_effect = [_scalar_result(workflow), _scalar_result(step), _scalar_result(job)]

    monkeypatch.setattr("backend.control_plane.adapters.workflows.authenticate_node_request", AsyncMock(return_value=SimpleNamespace(node_id="node-a")))

    with pytest.raises(HTTPException) as exc:
        await report_step_failed(
            "wf-1",
            "s1",
            WorkflowStepFailRequest(
                tenant_id="tenant-a",
                node_id="node-a",
                job_id="job-1",
                lease_token="lease-aaa",
                attempt=2,
                error="boom",
            ),
            db=db,
            node_token="token-a",
        )

    assert exc.value.status_code == 409
