"""Workflow API contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.extensions.job_kind_registry import validate_job_payload


class WorkflowStepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    kind: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, object] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = Field(default=60, ge=0, le=100)
    max_retries: int = Field(default=1, ge=0, le=10)

    @field_validator("depends_on")
    @classmethod
    def _validate_depends_on(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            dep = item.strip()
            if not dep:
                raise ValueError("depends_on entries must be non-empty step IDs")
            if dep in seen:
                raise ValueError("depends_on cannot contain duplicate step IDs")
            seen.add(dep)
            normalized.append(dep)
        return normalized

    @model_validator(mode="after")
    def _validate_payload_contract(self) -> WorkflowStepDefinition:
        try:
            self.payload = validate_job_payload(self.kind, dict(self.payload))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None)
    steps: list[WorkflowStepDefinition] = Field(..., min_length=1)


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
    steps_definition: list[dict[str, object]]
    steps_status: list[StepStatus]
    context: dict[str, object]


class WorkflowStepCompleteRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=64)
    node_id: str = Field(..., min_length=1, max_length=128)
    job_id: str = Field(..., min_length=1, max_length=128)
    lease_token: str = Field(..., min_length=8, max_length=256)
    attempt: int = Field(..., ge=1)
    result: dict = Field(default_factory=dict)


class WorkflowStepFailRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=64)
    node_id: str = Field(..., min_length=1, max_length=128)
    job_id: str = Field(..., min_length=1, max_length=128)
    lease_token: str = Field(..., min_length=8, max_length=256)
    attempt: int = Field(..., ge=1)
    error: str = Field(..., min_length=1, max_length=1024)
