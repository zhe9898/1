from __future__ import annotations

import datetime
import os
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.action_contracts import ControlAction, optional_reason_field
from backend.api.control_events import publish_control_event
from backend.api.deps import (
    get_current_admin,
    get_current_user,
    get_machine_tenant_db,
    get_node_machine_token,
    get_redis,
    get_tenant_db,
)
from backend.api.ui_contracts import FormFieldOption, FormFieldSchema, FormSectionSchema, ResourceSchemaResponse, StatusView
from backend.core.control_plane_state import (
    node_attention_reason,
    node_capacity_state,
    node_capacity_state_view,
    node_drain_status_view,
    node_enrollment_status_view,
    node_heartbeat_state,
    node_heartbeat_state_view,
    node_status_view,
)
from backend.core.errors import zen
from backend.core.gateway_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.core.node_auth import authenticate_node_request, generate_node_token, hash_node_token
from backend.core.protocol_version import validate_lease_version, validate_protocol_version
from backend.core.quota import check_node_quota
from backend.core.redis_client import CHANNEL_NODE_EVENTS, RedisClient
from backend.models.job import Job
from backend.models.node import Node

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


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


def _build_node_actions(node: Node) -> list[ControlAction]:
    can_drain = node.enrollment_status == "active" and (node.drain_status or "active") == "active"
    can_undrain = (node.drain_status or "active") != "active"
    can_rotate = node.enrollment_status != "revoked"
    can_revoke = node.enrollment_status != "revoked"
    return [
        ControlAction(
            key="rotate_token",
            label="Rotate Token",
            endpoint=f"/v1/nodes/{node.node_id}/token",
            enabled=can_rotate,
            reason=None if can_rotate else "Revoked nodes must be reprovisioned before rotating credentials",
            confirmation="Generate a new node token and invalidate the old one?",
        ),
        ControlAction(
            key="revoke",
            label="Revoke",
            endpoint=f"/v1/nodes/{node.node_id}/revoke",
            enabled=can_revoke,
            reason=None if can_revoke else "Node is already revoked",
            confirmation="Revoke this node and block it from pulling more work?",
        ),
        ControlAction(
            key="drain",
            label="Drain",
            endpoint=f"/v1/nodes/{node.node_id}/drain",
            enabled=can_drain,
            reason=(
                None
                if can_drain
                else ("Node must be actively enrolled before it can be drained" if node.enrollment_status != "active" else "Node is already draining")
            ),
            confirmation="Stop assigning new jobs to this node?",
            fields=[optional_reason_field()],
        ),
        ControlAction(
            key="undrain",
            label="Undrain",
            endpoint=f"/v1/nodes/{node.node_id}/undrain",
            enabled=can_undrain,
            reason=None if can_undrain else "Node is already active",
            confirmation="Return this node to the scheduler?",
            fields=[optional_reason_field()],
        ),
    ]


def _resource_schema() -> ResourceSchemaResponse:
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    return ResourceSchemaResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        resource="nodes",
        title="Nodes",
        description="Provision runners, issue one-time machine credentials, and govern fleet state from backend-owned contracts.",
        empty_state="No nodes match the current view.",
        policies={
            "ui_mode": "backend-driven",
            "resource_mode": "fleet-management",
            "list_query_filters": {
                "node_id": "exact",
                "node_type": "exact",
                "executor": "exact",
                "os": "exact",
                "zone": "exact",
                "enrollment_status": "exact",
                "drain_status": "derived",
                "heartbeat_state": "derived",
                "capacity_state": "derived",
                "attention": "derived-flag",
            },
            "submit_encoding": {
                "capabilities": "tags",
                "metadata": "json",
            },
            "secret_delivery": {
                "field": "node_token",
                "version_field": "auth_token_version",
                "visibility": "one-time",
            },
        },
        submit_action=ControlAction(
            key="provision",
            label="Provision Node",
            endpoint="/v1/nodes",
            method="POST",
            enabled=True,
            requires_admin=True,
        ),
        sections=[
            FormSectionSchema(
                id="identity",
                label="Identity",
                description="Create a fleet record and one-time machine credential for a new runner.",
                fields=[
                    FormFieldSchema(key="node_id", label="Node ID", required=True, placeholder="mac-mini-01"),
                    FormFieldSchema(key="name", label="Name", required=True, placeholder="Mac Mini 01"),
                    FormFieldSchema(
                        key="node_type",
                        label="Node Type",
                        input_type="select",
                        value="runner",
                        options=[
                            FormFieldOption(value="runner", label="Runner"),
                            FormFieldOption(value="sidecar", label="Sidecar"),
                            FormFieldOption(value="native-client", label="Native Client"),
                        ],
                    ),
                    FormFieldSchema(
                        key="address",
                        label="Address",
                        input_type="url",
                        placeholder="https://runner.example.invalid or http://10.0.0.12:9000",
                    ),
                ],
            ),
            FormSectionSchema(
                id="runtime",
                label="Runtime",
                description="Declare the execution contract the scheduler should trust before the first heartbeat arrives.",
                fields=[
                    FormFieldSchema(key="profile", label="Profile", value="go-runner", required=True),
                    FormFieldSchema(
                        key="executor",
                        label="Executor",
                        input_type="select",
                        value="go-native",
                        options=[
                            FormFieldOption(value="go-native", label="Go Native"),
                            FormFieldOption(value="python-runner", label="Python Runner"),
                            FormFieldOption(value="shell", label="Shell"),
                            FormFieldOption(value="swift-native", label="Swift Native"),
                            FormFieldOption(value="kotlin-native", label="Kotlin Native"),
                            FormFieldOption(value="vector-worker", label="Vector Worker"),
                            FormFieldOption(value="search-service", label="Search Service"),
                            FormFieldOption(value="unknown", label="Unknown"),
                        ],
                    ),
                    FormFieldSchema(
                        key="os",
                        label="OS",
                        input_type="select",
                        value="windows",
                        options=[
                            FormFieldOption(value="windows", label="Windows"),
                            FormFieldOption(value="darwin", label="macOS"),
                            FormFieldOption(value="linux", label="Linux"),
                            FormFieldOption(value="ios", label="iOS"),
                            FormFieldOption(value="android", label="Android"),
                            FormFieldOption(value="unknown", label="Unknown"),
                        ],
                    ),
                    FormFieldSchema(
                        key="arch",
                        label="Arch",
                        input_type="select",
                        value="amd64",
                        options=[
                            FormFieldOption(value="amd64", label="amd64"),
                            FormFieldOption(value="arm64", label="arm64"),
                            FormFieldOption(value="unknown", label="unknown"),
                        ],
                    ),
                    FormFieldSchema(key="zone", label="Zone", placeholder="home-lab"),
                    FormFieldSchema(key="protocol_version", label="Runner Contract", value="runner.v1"),
                    FormFieldSchema(key="lease_version", label="Lease Contract", value="job-lease.v1"),
                    FormFieldSchema(key="agent_version", label="Agent Version", placeholder="runner-agent 0.1.0"),
                    FormFieldSchema(key="max_concurrency", label="Max Concurrency", input_type="number", value=1),
                ],
            ),
            FormSectionSchema(
                id="resources",
                label="Resources",
                description="Declare explicit capacity so heterogeneous dispatch can respect executor and resource selectors.",
                fields=[
                    FormFieldSchema(key="cpu_cores", label="CPU Cores", input_type="number", value=0),
                    FormFieldSchema(key="memory_mb", label="Memory (MB)", input_type="number", value=0),
                    FormFieldSchema(key="gpu_vram_mb", label="GPU VRAM (MB)", input_type="number", value=0),
                    FormFieldSchema(key="storage_mb", label="Storage (MB)", input_type="number", value=0),
                ],
            ),
            FormSectionSchema(
                id="capabilities",
                label="Capabilities",
                description="Seed scheduler selectors and operator notes before the node comes online.",
                fields=[
                    FormFieldSchema(
                        key="capabilities",
                        label="Capabilities",
                        input_type="tags",
                        placeholder="job.execute,connector.invoke",
                    ),
                    FormFieldSchema(
                        key="metadata",
                        label="Metadata",
                        input_type="json",
                        value="{}",
                        placeholder='{"runtime":"go","managed_by":"console"}',
                    ),
                ],
            ),
        ],
    )


def _to_response(node: Node, *, active_lease_count: int = 0, now: datetime.datetime | None = None) -> NodeResponse:
    current_time = now or _utcnow()
    max_concurrency = max(int(node.max_concurrency or 1), 1)
    drain_status = node.drain_status or "active"
    heartbeat_state = node_heartbeat_state(node.last_seen_at, current_time)
    capacity_state = node_capacity_state(active_lease_count, max_concurrency)
    return NodeResponse(
        node_id=node.node_id,
        name=node.name,
        node_type=node.node_type,
        address=node.address,
        profile=node.profile,
        executor=node.executor,
        os=node.os,
        arch=node.arch,
        zone=node.zone,
        protocol_version=node.protocol_version,
        lease_version=node.lease_version,
        agent_version=node.agent_version,
        max_concurrency=max_concurrency,
        active_lease_count=max(int(active_lease_count), 0),
        cpu_cores=max(int(node.cpu_cores or 0), 0),
        memory_mb=max(int(node.memory_mb or 0), 0),
        gpu_vram_mb=max(int(node.gpu_vram_mb or 0), 0),
        storage_mb=max(int(node.storage_mb or 0), 0),
        drain_status=drain_status,
        drain_status_view=StatusView(**node_drain_status_view(drain_status)),
        health_reason=node.health_reason,
        heartbeat_state=heartbeat_state,
        heartbeat_state_view=StatusView(**node_heartbeat_state_view(heartbeat_state)),
        capacity_state=capacity_state,
        capacity_state_view=StatusView(**node_capacity_state_view(capacity_state)),
        attention_reason=node_attention_reason(
            enrollment_status=node.enrollment_status,
            status=node.status,
            drain_status=drain_status,
            heartbeat_state=heartbeat_state,
            capacity_state=capacity_state,
            health_reason=node.health_reason,
        ),
        enrollment_status=node.enrollment_status,
        enrollment_status_view=StatusView(**node_enrollment_status_view(node.enrollment_status)),
        status=node.status,
        status_view=StatusView(**node_status_view(node.status)),
        capabilities=list(node.capabilities or []),
        metadata=dict(node.metadata_json or {}),
        actions=_build_node_actions(node),
        registered_at=node.registered_at,
        last_seen_at=node.last_seen_at,
    )


def _apply_contract(node: Node, payload: NodeContractPayload, status: str, now: datetime.datetime) -> None:
    # Validate protocol versions before applying contract
    try:
        validated_protocol_version = validate_protocol_version(payload.protocol_version)
        validated_lease_version = validate_lease_version(payload.lease_version)
    except ValueError as e:
        raise zen(
            "ZEN-NODE-4001",
            str(e),
            status_code=400,
            recovery_hint="Upgrade runner-agent to a supported version",
            details={
                "node_id": payload.node_id,
                "protocol_version": payload.protocol_version,
                "lease_version": payload.lease_version,
            },
        ) from e

    node.tenant_id = payload.tenant_id
    node.name = payload.name
    node.node_type = payload.node_type
    node.address = payload.address
    node.profile = payload.profile
    node.executor = payload.executor
    node.os = payload.os
    node.arch = payload.arch
    node.zone = payload.zone
    node.protocol_version = validated_protocol_version
    node.lease_version = validated_lease_version
    node.agent_version = payload.agent_version
    node.max_concurrency = payload.max_concurrency
    node.cpu_cores = payload.cpu_cores
    node.memory_mb = payload.memory_mb
    node.gpu_vram_mb = payload.gpu_vram_mb
    node.storage_mb = payload.storage_mb
    node.status = status
    node.capabilities = payload.capabilities
    node.metadata_json = payload.metadata
    # Edge computing attributes (optional, with defaults)
    node.accepted_kinds = getattr(payload, "accepted_kinds", None) or []
    node.network_latency_ms = getattr(payload, "network_latency_ms", None)
    node.bandwidth_mbps = getattr(payload, "bandwidth_mbps", None)
    node.cached_data_keys = getattr(payload, "cached_data_keys", None) or []
    node.power_capacity_watts = getattr(payload, "power_capacity_watts", None)
    node.current_power_watts = getattr(payload, "current_power_watts", None)
    node.thermal_state = getattr(payload, "thermal_state", None)
    node.cloud_connectivity = getattr(payload, "cloud_connectivity", None)
    node.last_seen_at = now
    node.updated_at = now


def _build_bootstrap_gateway_base_url() -> str:
    gateway_base_url = os.getenv("NODE_BOOTSTRAP_GATEWAY_BASE_URL", "<gateway-base-url>").strip()
    return gateway_base_url or "<gateway-base-url>"


def _bootstrap_requires_insecure_http_opt_in(gateway_base_url: str) -> bool:
    parsed = urlparse(gateway_base_url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _build_bootstrap_commands(node: Node, node_token: str) -> dict[str, str]:
    gateway_base_url = _build_bootstrap_gateway_base_url()
    powershell_lines = [
        f'$env:RUNNER_NODE_ID="{node.node_id}"',
        f'$env:RUNNER_TENANT_ID="{node.tenant_id}"',
        f'$env:NODE_TOKEN="{node_token}"',
        f'$env:GATEWAY_BASE_URL="{gateway_base_url}"',
        f'$env:RUNNER_EXECUTOR="{node.executor}"',
    ]
    unix_lines = [
        f'export RUNNER_NODE_ID="{node.node_id}"',
        f'export RUNNER_TENANT_ID="{node.tenant_id}"',
        f'export NODE_TOKEN="{node_token}"',
        f'export GATEWAY_BASE_URL="{gateway_base_url}"',
        f'export RUNNER_EXECUTOR="{node.executor}"',
    ]
    if _bootstrap_requires_insecure_http_opt_in(gateway_base_url):
        powershell_lines.append('$env:RUNNER_ALLOW_INSECURE_HTTP="true"')
        unix_lines.append('export RUNNER_ALLOW_INSECURE_HTTP="true"')
    powershell_lines.append(".\\runner-agent.exe")
    unix_lines.append("./runner-agent")
    return {
        "powershell": "\n".join(powershell_lines),
        "unix": "\n".join(unix_lines),
    }


def _bootstrap_notes() -> list[str]:
    return [
        "请把 <gateway-base-url> 替换为当前网关对外可达的根地址；runner 会自行拼接 /api/v1/... 控制面路径。",
        "机器通道默认要求 HTTPS；只有在本机开发联调时，才允许配合 RUNNER_ALLOW_INSECURE_HTTP=true 使用 http://127.0.0.1...",
        "请同时保留 RUNNER_TENANT_ID；机器通道会在鉴权前先绑定租户上下文。",
        "一次性 node token 只能保存在节点主机或原生客户端本地；当前回执关闭后不会再次展示。",
    ]


def _build_bootstrap_receipts(node: Node, node_token: str) -> list[BootstrapReceipt]:
    gateway_base_url = _build_bootstrap_gateway_base_url()
    receipts: list[BootstrapReceipt] = []
    bootstrap_commands = _build_bootstrap_commands(node, node_token)

    if node.node_type != "native-client" and node.os in {"windows", "unknown"}:
        receipts.append(
            BootstrapReceipt(
                key="powershell",
                label="Windows / PowerShell",
                platform="windows",
                kind="command",
                content=bootstrap_commands["powershell"],
                notes=["适用于 Windows Runner 节点。"],
            )
        )
    if node.node_type != "native-client" and node.os in {"darwin", "linux", "unknown"}:
        receipts.append(
            BootstrapReceipt(
                key="unix",
                label="macOS / Linux",
                platform="unix",
                kind="command",
                content=bootstrap_commands["unix"],
                notes=["适用于 macOS 或 Linux Runner 节点。"],
            )
        )

    native_common = {
        "node_id": node.node_id,
        "tenant_id": node.tenant_id,
        "node_token": node_token,
        "gateway_base_url": gateway_base_url,
        "executor": node.executor,
        "zone": node.zone or "mobile",
    }
    if node.node_type == "native-client" or node.os in {"ios", "android"} or node.executor in {"swift-native", "kotlin-native"}:
        if node.os in {"ios", "unknown"} or node.executor == "swift-native" or node.node_type == "native-client":
            receipts.append(
                BootstrapReceipt(
                    key="ios-native",
                    label="iOS 原生客户端",
                    platform="ios",
                    kind="json-config",
                    content=(
                        "{\n"
                        f'  "node_id": "{native_common["node_id"]}",\n'
                        f'  "tenant_id": "{native_common["tenant_id"]}",\n'
                        f'  "node_token": "{native_common["node_token"]}",\n'
                        f'  "gateway_base_url": "{native_common["gateway_base_url"]}",\n'
                        '  "native_bridge": ["health.ingest", "notify.push", "device.local"],\n'
                        f'  "executor": "{native_common["executor"]}",\n'
                        f'  "zone": "{native_common["zone"]}"\n'
                        "}"
                    ),
                    notes=["写入 iOS 原生客户端配置，供 HealthKit、通知和本地能力桥复用控制面合同。"],
                )
            )
        if node.os in {"android", "unknown"} or node.executor == "kotlin-native" or node.node_type == "native-client":
            receipts.append(
                BootstrapReceipt(
                    key="android-native",
                    label="Android 原生客户端",
                    platform="android",
                    kind="json-config",
                    content=(
                        "{\n"
                        f'  "node_id": "{native_common["node_id"]}",\n'
                        f'  "tenant_id": "{native_common["tenant_id"]}",\n'
                        f'  "node_token": "{native_common["node_token"]}",\n'
                        f'  "gateway_base_url": "{native_common["gateway_base_url"]}",\n'
                        '  "native_bridge": ["health.ingest", "notify.push", "device.local"],\n'
                        f'  "executor": "{native_common["executor"]}",\n'
                        f'  "zone": "{native_common["zone"]}"\n'
                        "}"
                    ),
                    notes=["写入 Android 原生客户端配置，供 Health Connect、通知和本地能力桥复用控制面合同。"],
                )
            )
    return receipts


def _matches_node_list_filters(
    node: Node,
    *,
    active_lease_count: int,
    now: datetime.datetime,
    node_type: str | None,
    executor: str | None,
    os_name: str | None,
    zone: str | None,
    enrollment_status: str | None,
    drain_status: str | None,
    heartbeat_state: str | None,
    capacity_state: str | None,
    attention: str | None,
) -> bool:
    node_drain = node.drain_status or "active"
    node_heartbeat = node_heartbeat_state(node.last_seen_at, now)
    node_capacity = node_capacity_state(active_lease_count, max(int(node.max_concurrency or 1), 1))
    attention_reason = node_attention_reason(
        enrollment_status=node.enrollment_status,
        status=node.status,
        drain_status=node_drain,
        heartbeat_state=node_heartbeat,
        capacity_state=node_capacity,
        health_reason=node.health_reason,
    )
    if node_type and node.node_type != node_type:
        return False
    if executor and node.executor != executor:
        return False
    if os_name and node.os != os_name:
        return False
    if zone and (node.zone or "") != zone:
        return False
    if enrollment_status and node.enrollment_status != enrollment_status:
        return False
    if drain_status and node_drain != drain_status:
        return False
    if heartbeat_state and node_heartbeat != heartbeat_state:
        return False
    if capacity_state and node_capacity != capacity_state:
        return False
    if attention == "attention" and attention_reason is None:
        return False
    return True


async def _get_node_by_id(db: AsyncSession, tenant_id: str, node_id: str) -> Node:
    result = await db.execute(select(Node).where(Node.tenant_id == tenant_id, Node.node_id == node_id))
    node = result.scalars().first()
    if node is None:
        raise zen("ZEN-NODE-4040", "node not found", status_code=404)
    return node


async def _get_active_lease_counts(
    db: AsyncSession,
    *,
    tenant_id: str,
    node_ids: list[str],
    now: datetime.datetime,
) -> dict[str, int]:
    if not node_ids:
        return {}
    result = await db.execute(
        select(Job.node_id, func.count())
        .where(
            Job.tenant_id == tenant_id,
            Job.node_id.in_(node_ids),
            Job.status == "leased",
            Job.leased_until.is_not(None),
            Job.leased_until > now,
        )
        .group_by(Job.node_id)
    )
    return {str(node_id): int(count or 0) for node_id, count in result.all() if node_id}


def _provision_token(node: Node) -> tuple[str, int]:
    token = generate_node_token()
    next_version = int(node.auth_token_version or 0) + 1
    node.auth_token_hash = hash_node_token(token)
    node.auth_token_version = next_version
    return token, next_version


@router.get("/schema", response_model=ResourceSchemaResponse)
async def get_node_schema(
    current_user: dict[str, object] = Depends(get_current_admin),
) -> ResourceSchemaResponse:
    del current_user
    return _resource_schema()


@router.post("", response_model=NodeProvisionResponse)
async def provision_node(
    payload: NodeProvisionRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeProvisionResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    await check_node_quota(db, tenant_id)
    existing = await db.execute(select(Node).where(Node.tenant_id == tenant_id, Node.node_id == payload.node_id))
    if existing.scalars().first() is not None:
        raise zen(
            "ZEN-NODE-4090",
            "node already exists",
            status_code=409,
            recovery_hint="Use token rotation for an existing node instead of provisioning again",
            details={"node_id": payload.node_id},
        )

    now = _utcnow()
    node = Node(
        tenant_id=tenant_id,
        node_id=payload.node_id,
        registered_at=now,
        last_seen_at=now,
        enrollment_status="pending",
        status="offline",
        max_concurrency=payload.max_concurrency,
        drain_status="active",
    )
    _apply_contract(node, payload.model_copy(update={"tenant_id": tenant_id}), "offline", now)
    node.enrollment_status = "pending"
    token, version = _provision_token(node)
    db.add(node)
    await db.flush()
    return NodeProvisionResponse(
        node=_to_response(node, now=now),
        node_token=token,
        auth_token_version=version,
        bootstrap_commands=_build_bootstrap_commands(node, token),
        bootstrap_notes=_bootstrap_notes(),
        bootstrap_receipts=_build_bootstrap_receipts(node, token),
    )


@router.post("/{id}/token", response_model=NodeProvisionResponse)
async def rotate_node_token(
    id: str,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeProvisionResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    token, version = _provision_token(node)
    node.enrollment_status = "pending"
    node.status = "offline"
    now = _utcnow()
    node.updated_at = now
    await db.flush()
    return NodeProvisionResponse(
        node=_to_response(node, now=now),
        node_token=token,
        auth_token_version=version,
        bootstrap_commands=_build_bootstrap_commands(node, token),
        bootstrap_notes=_bootstrap_notes(),
        bootstrap_receipts=_build_bootstrap_receipts(node, token),
    )


@router.post("/{id}/revoke", response_model=NodeResponse)
async def revoke_node(
    id: str,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    node.auth_token_hash = None
    node.auth_token_version = int(node.auth_token_version or 0) + 1
    node.enrollment_status = "revoked"
    node.status = "offline"
    now = _utcnow()
    node.updated_at = now
    await db.flush()
    return _to_response(node, now=now)


@router.post("/{id}/drain", response_model=NodeResponse)
async def drain_node(
    id: str,
    payload: NodeDrainRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    node.drain_status = "draining"
    node.health_reason = payload.reason or node.health_reason
    node.updated_at = _utcnow()
    await db.flush()
    response = _to_response(node, now=node.updated_at)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "drain",
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.post("/{id}/undrain", response_model=NodeResponse)
async def undrain_node(
    id: str,
    payload: NodeDrainRequest,
    current_user: dict[str, object] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    node.drain_status = "active"
    node.health_reason = payload.reason
    node.updated_at = _utcnow()
    await db.flush()
    response = _to_response(node, now=node.updated_at)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "undrain",
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.post("/register", response_model=NodeResponse)
async def register_node(
    payload: NodeRegisterRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> NodeResponse:
    node = await authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=False,
        tenant_id=payload.tenant_id,
    )
    event_action = "updated" if node.enrollment_status == "active" else "registered"
    now = _utcnow()
    _apply_contract(node, payload, "online", now)
    # Enrollment approval: new nodes stay pending until admin approves.
    # Re-registration of already-active nodes keeps active status.
    if node.enrollment_status not in ("active",):
        node.enrollment_status = "pending"
    node.drain_status = "active"
    node.health_reason = None

    await db.flush()
    response = _to_response(node, now=now)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        event_action,
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.post("/heartbeat", response_model=NodeResponse)
async def heartbeat_node(
    payload: NodeHeartbeatRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> NodeResponse:
    node = await authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=False,
        tenant_id=payload.tenant_id,
    )

    # ── ADR-0047 WP-P0: 节点状态机封闭 ──────────────────────────────────────
    # heartbeat 绝不能成为绕过审批流的旁路。
    # pending → active 的唯一合法路径是 POST /api/v1/nodes/{node_id}/approve 管理员操作。
    # revoked 节点已吊销凭证，必须重新 provision + register。
    if node.enrollment_status == "pending":
        raise zen(
            "ZEN-NODE-4031",
            "Node is pending enrollment approval and cannot send heartbeats yet",
            status_code=403,
            recovery_hint="Wait for an admin to approve this node via POST /api/v1/nodes/{node_id}/approve",
            details={"node_id": node.node_id, "enrollment_status": node.enrollment_status},
        )
    if node.enrollment_status == "revoked":
        raise zen(
            "ZEN-NODE-4032",
            "Revoked node cannot send heartbeats; provision and re-register with a new token",
            status_code=403,
            recovery_hint="Provision a new node token and re-register before sending heartbeats",
            details={"node_id": node.node_id, "enrollment_status": node.enrollment_status},
        )
    # ── 仅 active 节点可续活，enrollment_status 保持不变 ─────────────────────

    now = _utcnow()
    _apply_contract(node, payload, payload.status, now)
    node.health_reason = payload.health_reason

    await db.flush()
    active_counts = await _get_active_lease_counts(db, tenant_id=payload.tenant_id, node_ids=[node.node_id], now=now)
    response = _to_response(node, active_lease_count=active_counts.get(node.node_id, 0), now=now)
    await publish_control_event(
        redis,
        CHANNEL_NODE_EVENTS,
        "heartbeat",
        {"node": response.model_dump(mode="json")},
    )
    return response


@router.get("", response_model=list[NodeResponse])
async def list_nodes(
    node_id: str | None = None,
    node_type: str | None = None,
    executor: str | None = None,
    os: str | None = None,
    zone: str | None = None,
    enrollment_status: str | None = None,
    drain_status: str | None = None,
    heartbeat_state: str | None = None,
    capacity_state: str | None = None,
    attention: str | None = None,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[NodeResponse]:
    tenant_id = str(current_user.get("tenant_id") or "default")
    query = select(Node).where(Node.tenant_id == tenant_id)
    if node_id:
        query = query.where(Node.node_id == node_id)
    if node_type:
        query = query.where(Node.node_type == node_type)
    if executor:
        query = query.where(Node.executor == executor)
    if os:
        query = query.where(Node.os == os)
    if zone:
        query = query.where(Node.zone == zone)
    if enrollment_status:
        query = query.where(Node.enrollment_status == enrollment_status)
    result = await db.execute(query.order_by(Node.last_seen_at.desc()))
    nodes = list(result.scalars().all())
    now = _utcnow()
    counts = await _get_active_lease_counts(db, tenant_id=tenant_id, node_ids=[node.node_id for node in nodes], now=now)
    filtered = [
        node
        for node in nodes
        if _matches_node_list_filters(
            node,
            active_lease_count=counts.get(node.node_id, 0),
            now=now,
            node_type=node_type,
            executor=executor,
            os_name=os,
            zone=zone,
            enrollment_status=enrollment_status,
            drain_status=drain_status,
            heartbeat_state=heartbeat_state,
            capacity_state=capacity_state,
            attention=attention,
        )
    ]
    return [_to_response(node, active_lease_count=counts.get(node.node_id, 0), now=now) for node in filtered]


@router.get("/{id}", response_model=NodeResponse)
async def get_node(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> NodeResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    node = await _get_node_by_id(db, tenant_id, id)
    now = _utcnow()
    counts = await _get_active_lease_counts(db, tenant_id=tenant_id, node_ids=[node.node_id], now=now)
    return _to_response(node, active_lease_count=counts.get(node.node_id, 0), now=now)
