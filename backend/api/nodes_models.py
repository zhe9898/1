"""
ZEN70 Nodes API – Pydantic models.

Split from nodes.py for maintainability.  All request/response schemas
and the bootstrap receipt model live here.
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

from backend.api.action_contracts import ControlAction
from backend.api.ui_contracts import StatusView


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


class NodeContractPayload(BaseModel):
    tenant_id: str = Field(default="default", min_length=1, max_length=64)
    node_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=128)
    node_type: str = Field(default="runner", min_length=1, max_length=64)
    address: str | None = Field(default=None, max_length=255)
    profile: str = Field(default="go-runner", min_length=1, max_length=64)
    executor: str = Field(default="unknown", min_length=1, max_length=64)
    os: str = Field(default="unknown", min_length=1, max_length=64)
    arch: str = Field(default="unknown", min_length=1, max_length=64)
    zone: str | None = Field(default=None, max_length=128)
    protocol_version: str = Field(default="runner.v1", min_length=1, max_length=32)
    lease_version: str = Field(default="job-lease.v1", min_length=1, max_length=32)
    agent_version: str | None = Field(default=None, max_length=64)
    max_concurrency: int = Field(default=1, ge=1, le=128)
    cpu_cores: int = Field(default=0, ge=0, le=4096)
    memory_mb: int = Field(default=0, ge=0, le=8_388_608)
    gpu_vram_mb: int = Field(default=0, ge=0, le=8_388_608)
    storage_mb: int = Field(default=0, ge=0, le=134_217_728)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    # Kind contract — which job kinds this node accepts
    accepted_kinds: list[str] = Field(default_factory=list)
    # Edge computing telemetry (dynamic, reported each heartbeat)
    network_latency_ms: int | None = Field(default=None, ge=0, le=60_000)
    bandwidth_mbps: int | None = Field(default=None, ge=0, le=100_000)
    cached_data_keys: list[str] = Field(default_factory=list)
    power_capacity_watts: int | None = Field(default=None, ge=0, le=100_000)
    current_power_watts: int | None = Field(default=None, ge=0, le=100_000)
    thermal_state: str | None = Field(default=None, pattern=r"^(normal|warm|hot|cool|throttling)$")
    cloud_connectivity: str | None = Field(default=None, pattern=r"^(online|degraded|offline|unknown)$")


class NodeProvisionRequest(NodeContractPayload):
    pass


class NodeRegisterRequest(NodeContractPayload):
    pass


class NodeHeartbeatRequest(NodeContractPayload):
    status: str = Field(default="online", min_length=1, max_length=32)
    health_reason: str | None = Field(default=None, max_length=255)


class NodeDrainRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class NodeResponse(BaseModel):
    node_id: str
    name: str
    node_type: str
    address: str | None
    profile: str
    executor: str
    os: str
    arch: str
    zone: str | None
    protocol_version: str
    lease_version: str
    agent_version: str | None
    max_concurrency: int
    active_lease_count: int
    cpu_cores: int
    memory_mb: int
    gpu_vram_mb: int
    storage_mb: int
    drain_status: str
    drain_status_view: StatusView
    health_reason: str | None
    heartbeat_state: str
    heartbeat_state_view: StatusView
    capacity_state: str
    capacity_state_view: StatusView
    attention_reason: str | None
    enrollment_status: str
    enrollment_status_view: StatusView
    status: str
    status_view: StatusView
    capabilities: list[str]
    metadata: dict[str, object]
    actions: list[ControlAction] = Field(default_factory=list)
    registered_at: datetime.datetime
    last_seen_at: datetime.datetime


class BootstrapReceipt(BaseModel):
    key: str
    label: str
    platform: str
    kind: str
    content: str
    notes: list[str] = Field(default_factory=list)


class NodeProvisionResponse(BaseModel):
    node: NodeResponse
    node_token: str
    auth_token_version: int
    bootstrap_commands: dict[str, str] = Field(default_factory=dict)
    bootstrap_notes: list[str] = Field(default_factory=list)
    bootstrap_receipts: list[BootstrapReceipt] = Field(default_factory=list)
