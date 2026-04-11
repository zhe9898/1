"""Connector API contracts."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

from backend.control_plane.adapters.action_contracts import ControlAction
from backend.control_plane.adapters.ui_contracts import StatusView


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
