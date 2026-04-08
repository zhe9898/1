from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.workflows import WorkflowCreateRequest
from backend.kernel.extensions.workflow_engine import create_workflow


def test_workflow_create_request_forbids_unknown_step_fields() -> None:
    with pytest.raises(ValidationError):
        WorkflowCreateRequest(
            name="wf",
            steps=[
                {
                    "id": "step-1",
                    "kind": "shell.exec",
                    "payload": {"command": "echo ok"},
                    "unexpected": "boom",
                }
            ],
        )


@pytest.mark.asyncio
async def test_create_workflow_rejects_invalid_step_payload() -> None:
    db = AsyncMock()
    db.add = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await create_workflow(
            db,
            tenant_id="tenant-a",
            name="wf",
            steps=[
                {
                    "id": "step-1",
                    "kind": "shell.exec",
                    "payload": {"command": 123},
                }
            ],
            created_by="admin",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ZEN-WF-4000"


@pytest.mark.asyncio
async def test_create_workflow_rejects_unsupported_step_keys() -> None:
    db = AsyncMock()
    db.add = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await create_workflow(
            db,
            tenant_id="tenant-a",
            name="wf",
            steps=[
                {
                    "id": "step-1",
                    "kind": "shell.exec",
                    "payload": {"command": "echo ok"},
                    "raw_sql": "drop table jobs",
                }
            ],
            created_by="admin",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "ZEN-WF-4000"
