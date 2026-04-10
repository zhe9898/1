"""Extension SDK discovery and workflow template APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_user, get_tenant_db
from backend.control_plane.adapters.workflows import StepStatus, WorkflowDetailResponse, _to_response
from backend.extensions.extension_sdk import (
    bootstrap_extension_runtime,
    get_extension_info,
    get_published_connector_kind,
    get_published_job_kind,
    get_published_workflow_template,
    list_extensions,
    list_published_connector_kinds,
    list_published_job_kinds,
    list_published_workflow_templates,
)
from backend.extensions.workflow_engine import create_workflow
from backend.extensions.workflow_template_registry import render_workflow_template
from backend.kernel.contracts.errors import zen
from backend.models.workflow import WorkflowStep

router = APIRouter(prefix="/api/v1/extensions", tags=["extensions"])


class CompatibilityResponse(BaseModel):
    min_kernel_version: str
    max_kernel_version: str | None = None
    supported_api_versions: list[str] = Field(default_factory=list)
    compatibility_mode: str
    notes: str = ""


class ExtensionSummaryResponse(BaseModel):
    extension_id: str
    version: str
    sdk_version: str
    name: str
    publisher: str
    description: str
    stability: str
    compatibility: CompatibilityResponse
    job_kinds: list[str]
    connector_kinds: list[str]
    workflow_templates: list[str]
    source_manifest_path: str | None = None


class PublishedKindResponse(BaseModel):
    kind: str
    has_payload_schema: bool | None = None
    has_result_schema: bool | None = None
    has_config_schema: bool | None = None
    payload_schema: dict[str, Any] | None = None
    result_schema: dict[str, Any] | None = None
    config_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowTemplateResponse(BaseModel):
    template_id: str
    version: str
    schema_version: str
    sdk_version: str
    display_name: str
    description: str
    parameters_schema: dict[str, Any] | None = None
    default_parameters: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]]
    labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowTemplateRenderRequest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkflowTemplateRenderResponse(BaseModel):
    template_id: str
    version: str
    display_name: str
    description: str
    parameters: dict[str, Any]
    steps: list[dict[str, Any]]


class WorkflowTemplateStartRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


def _to_extension_response(payload: dict[str, Any]) -> ExtensionSummaryResponse:
    return ExtensionSummaryResponse(**payload)


def _to_workflow_template_response(payload: dict[str, Any]) -> WorkflowTemplateResponse:
    return WorkflowTemplateResponse(**payload)


def _raise_template_error(exc: ValueError) -> None:
    code = "ZEN-EXT-4041" if "is not registered" in str(exc) else "ZEN-EXT-4001"
    status_code = 404 if code == "ZEN-EXT-4041" else 400
    raise zen(code, str(exc), status_code=status_code) from exc


@router.get("", response_model=list[ExtensionSummaryResponse])
async def list_registered_extensions(
    current_user: dict[str, object] = Depends(get_current_user),
) -> list[ExtensionSummaryResponse]:
    del current_user
    bootstrap_extension_runtime()
    return [_to_extension_response(item) for item in list_extensions()]


@router.get("/job-kinds", response_model=list[PublishedKindResponse])
async def list_registered_job_kinds(
    current_user: dict[str, object] = Depends(get_current_user),
) -> list[PublishedKindResponse]:
    del current_user
    return [PublishedKindResponse(**item) for item in list_published_job_kinds()]


@router.get("/job-kinds/{kind:path}", response_model=PublishedKindResponse)
async def get_registered_job_kind(
    kind: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> PublishedKindResponse:
    del current_user
    try:
        return PublishedKindResponse(**get_published_job_kind(kind))
    except ValueError as exc:
        raise zen("ZEN-EXT-4042", str(exc), status_code=404) from exc


@router.get("/connector-kinds", response_model=list[PublishedKindResponse])
async def list_registered_connector_kinds(
    current_user: dict[str, object] = Depends(get_current_user),
) -> list[PublishedKindResponse]:
    del current_user
    return [PublishedKindResponse(**item) for item in list_published_connector_kinds()]


@router.get("/connector-kinds/{kind:path}", response_model=PublishedKindResponse)
async def get_registered_connector_kind(
    kind: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> PublishedKindResponse:
    del current_user
    try:
        return PublishedKindResponse(**get_published_connector_kind(kind))
    except ValueError as exc:
        raise zen("ZEN-EXT-4043", str(exc), status_code=404) from exc


@router.get("/workflow-templates", response_model=list[WorkflowTemplateResponse])
async def list_registered_workflow_templates(
    current_user: dict[str, object] = Depends(get_current_user),
) -> list[WorkflowTemplateResponse]:
    del current_user
    return [_to_workflow_template_response(item) for item in list_published_workflow_templates()]


@router.get("/workflow-templates/{template_id}", response_model=WorkflowTemplateResponse)
async def get_registered_workflow_template(
    template_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> WorkflowTemplateResponse:
    del current_user
    try:
        return _to_workflow_template_response(get_published_workflow_template(template_id))
    except ValueError as exc:
        raise zen("ZEN-EXT-4041", str(exc), status_code=404) from exc


@router.post("/workflow-templates/{template_id}/render", response_model=WorkflowTemplateRenderResponse)
async def render_registered_workflow_template(
    template_id: str,
    payload: WorkflowTemplateRenderRequest,
    current_user: dict[str, object] = Depends(get_current_user),
) -> WorkflowTemplateRenderResponse:
    del current_user
    bootstrap_extension_runtime()
    try:
        rendered = render_workflow_template(template_id, payload.parameters)
    except ValueError as exc:
        _raise_template_error(exc)
    return WorkflowTemplateRenderResponse(**rendered)


@router.post("/workflow-templates/{template_id}/start", response_model=WorkflowDetailResponse)
async def start_registered_workflow_template(
    template_id: str,
    payload: WorkflowTemplateStartRequest,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> WorkflowDetailResponse:
    bootstrap_extension_runtime()
    try:
        rendered = render_workflow_template(template_id, payload.parameters)
    except ValueError as exc:
        _raise_template_error(exc)

    workflow = await create_workflow(
        db,
        tenant_id=current_user["tenant_id"],
        name=payload.name or rendered["display_name"],
        description=payload.description or rendered["description"],
        steps=rendered["steps"],
        created_by=current_user.get("username"),
    )

    steps_result = await db.execute(select(WorkflowStep).where(WorkflowStep.workflow_id_fk == workflow.id))
    steps_status = [
        StepStatus(
            step_id=ws.step_id,
            job_id=ws.job_id,
            status=ws.status,
            result=ws.result,
            error_message=ws.error_message,
            started_at=ws.started_at.isoformat() if ws.started_at else None,
            completed_at=ws.completed_at.isoformat() if ws.completed_at else None,
        )
        for ws in steps_result.scalars().all()
    ]
    return WorkflowDetailResponse(
        **_to_response(workflow).model_dump(),
        steps_definition=workflow.steps,
        steps_status=steps_status,
        context=workflow.context,
    )


@router.get("/{extension_id}", response_model=ExtensionSummaryResponse)
async def get_registered_extension(
    extension_id: str,
    current_user: dict[str, object] = Depends(get_current_user),
) -> ExtensionSummaryResponse:
    del current_user
    bootstrap_extension_runtime()
    try:
        return _to_extension_response(get_extension_info(extension_id))
    except ValueError as exc:
        raise zen("ZEN-EXT-4040", str(exc), status_code=404) from exc
