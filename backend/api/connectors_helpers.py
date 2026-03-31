"""Connector Pydantic models, schema, and helper functions.

Extracted from connectors.py for maintainability.
Route handlers remain in connectors.py.
"""
from __future__ import annotations

import datetime
import os

from pydantic import BaseModel, Field

from backend.api.action_contracts import ControlAction, ControlActionField
from backend.api.ui_contracts import FormFieldOption, FormFieldSchema, FormSectionSchema, ResourceSchemaResponse, StatusView
from backend.core.control_plane_state import connector_status_view
from backend.core.gateway_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.models.connector import Connector


# ── Pydantic request/response models ─────────────────────────────────


class ConnectorUpsertRequest(BaseModel):
    connector_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=128)
    kind: str = Field(..., min_length=1, max_length=64)
    status: str = "configured"
    endpoint: str | None = None
    profile: str = "manual"
    config: dict[str, object] = Field(default_factory=dict)


class ConnectorResponse(BaseModel):
    connector_id: str
    name: str
    kind: str
    status: str
    status_view: StatusView
    endpoint: str | None
    profile: str
    config: dict[str, object]
    last_test_ok: bool | None
    last_test_status: str | None
    last_test_message: str | None
    last_test_at: datetime.datetime | None
    last_invoke_status: str | None
    last_invoke_message: str | None
    last_invoke_job_id: str | None
    last_invoke_at: datetime.datetime | None
    attention_reason: str | None
    actions: list[ControlAction] = Field(default_factory=list)
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConnectorInvokeRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, object] = Field(default_factory=dict)
    lease_seconds: int = Field(default=30, ge=5, le=3600)


class ConnectorInvokeResponse(BaseModel):
    connector_id: str
    accepted: bool
    job_id: str
    status: str
    message: str


class ConnectorTestRequest(BaseModel):
    timeout_ms: int = Field(default=1500, ge=100, le=10000)


class ConnectorTestResponse(BaseModel):
    connector_id: str
    ok: bool
    endpoint: str | None
    status: str
    message: str
    checked_at: datetime.datetime


# ── Helper functions ──────────────────────────────────────────────────


def _to_response(connector: Connector) -> ConnectorResponse:
    status = connector.status
    return ConnectorResponse(
        connector_id=connector.connector_id,
        name=connector.name,
        kind=connector.kind,
        status=connector.status,
        status_view=StatusView(**connector_status_view(status)),
        endpoint=connector.endpoint,
        profile=connector.profile,
        config=dict(connector.config or {}),
        last_test_ok=connector.last_test_ok,
        last_test_status=connector.last_test_status,
        last_test_message=connector.last_test_message,
        last_test_at=connector.last_test_at,
        last_invoke_status=connector.last_invoke_status,
        last_invoke_message=connector.last_invoke_message,
        last_invoke_job_id=connector.last_invoke_job_id,
        last_invoke_at=connector.last_invoke_at,
        attention_reason=_connector_attention_reason(connector),
        actions=_build_connector_actions(connector),
        created_at=connector.created_at,
        updated_at=connector.updated_at,
    )


def _build_connector_actions(connector: Connector) -> list[ControlAction]:
    status = connector.status
    can_test = status in {"configured", "online", "healthy", "auth_required", "error"}
    can_invoke = status in {"configured", "online", "healthy"}
    return [
        ControlAction(
            key="test",
            label="Test",
            endpoint=f"/v1/connectors/{connector.connector_id}/test",
            method="POST",
            enabled=can_test,
            requires_admin=False,
            reason=None if can_test else f"Connector status {status} does not allow testing",
            confirmation=None,
            fields=[
                ControlActionField(
                    key="timeout_ms",
                    label="Timeout (ms)",
                    input_type="number",
                    required=False,
                    placeholder="1500",
                    value=1500,
                )
            ],
        ),
        ControlAction(
            key="invoke",
            label="Invoke",
            endpoint=f"/v1/connectors/{connector.connector_id}/invoke",
            method="POST",
            enabled=can_invoke,
            requires_admin=False,
            reason=None if can_invoke else f"Connector status {status} is not ready for invoke",
            confirmation=None,
            fields=[
                ControlActionField(
                    key="action",
                    label="Action",
                    input_type="text",
                    required=True,
                    placeholder="ping",
                    value="ping",
                ),
                ControlActionField(
                    key="payload",
                    label="Payload JSON",
                    input_type="json",
                    required=False,
                    placeholder='{"from":"gateway-console"}',
                    value='{"from":"gateway-console"}',
                ),
                ControlActionField(
                    key="lease_seconds",
                    label="Lease Seconds",
                    input_type="number",
                    required=False,
                    placeholder="30",
                    value=30,
                ),
            ],
        ),
    ]


def _connector_attention_reason(connector: Connector) -> str | None:
    if connector.status in {"error", "auth_required"}:
        return connector.last_test_message or f"connector status={connector.status}"
    if connector.last_test_ok is False:
        return connector.last_test_message or "latest connector test failed"
    if connector.status == "configured":
        return "connector configured but not yet confirmed healthy"
    return None


def _matches_connector_list_filters(
    connector: Connector,
    *,
    status: str | None,
    attention: str | None,
) -> bool:
    if status and connector_status_view(connector.status)["key"] != status:
        return False
    if attention == "attention" and _connector_attention_reason(connector) is None:
        return False
    return True


def _resource_schema() -> ResourceSchemaResponse:
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    return ResourceSchemaResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        resource="connectors",
        title="Connectors",
        description="Register integrations, persist health, and run test or invoke actions from backend-owned contracts.",
        empty_state="No connectors match the current view.",
        policies={
            "ui_mode": "backend-driven",
            "resource_mode": "integration-center",
            "list_query_filters": {
                "connector_id": "exact",
                "status": "status-view",
                "attention": "derived-flag",
            },
            "submit_encoding": {"config": "json"},
        },
        submit_action=ControlAction(
            key="upsert",
            label="Save Connector",
            endpoint="/v1/connectors",
            method="POST",
            enabled=True,
            requires_admin=False,
            reason=None,
            confirmation=None,
            fields=[],
        ),
        sections=[
            FormSectionSchema(
                id="identity",
                label="Identity",
                description="Connector identity and integration type are backend-owned contract fields.",
                fields=[
                    FormFieldSchema(key="connector_id", label="Connector ID", required=True, placeholder="connector-id"),
                    FormFieldSchema(key="name", label="Name", required=True, placeholder="Kitchen Relay"),
                    FormFieldSchema(
                        key="kind",
                        label="Kind",
                        input_type="select",
                        required=True,
                        value="runner",
                        options=[
                            FormFieldOption(value="runner", label="Runner"),
                            FormFieldOption(value="http", label="HTTP"),
                            FormFieldOption(value="mqtt", label="MQTT"),
                            FormFieldOption(value="manual", label="Manual"),
                        ],
                    ),
                ],
            ),
            FormSectionSchema(
                id="runtime",
                label="Runtime",
                description="Profile, endpoint, status, and config shape are provided by the backend contract.",
                fields=[
                    FormFieldSchema(
                        key="status",
                        label="Status",
                        input_type="select",
                        required=True,
                        value="configured",
                        options=[
                            FormFieldOption(value="configured", label="Configured"),
                            FormFieldOption(value="online", label="Online"),
                            FormFieldOption(value="healthy", label="Healthy"),
                            FormFieldOption(value="auth_required", label="Auth Required"),
                            FormFieldOption(value="error", label="Error"),
                        ],
                    ),
                    FormFieldSchema(key="endpoint", label="Endpoint", input_type="url", placeholder="https://endpoint.example"),
                    FormFieldSchema(key="profile", label="Profile", value="manual", placeholder="manual"),
                    FormFieldSchema(
                        key="config",
                        label="Config JSON",
                        input_type="json",
                        required=False,
                        placeholder='{"headers":{"x-api-key":"..."} }',
                        value="{}",
                    ),
                ],
            ),
        ],
    )
