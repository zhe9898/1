"""Console helper models and builder functions.

Extracted from console.py for maintainability. Contains all Pydantic
view-models and pure helper functions used by console route handlers.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

from backend.api.action_contracts import ControlAction
from backend.api.ui_contracts import StatusView
from backend.control_plane.console.manifest_service import iter_control_plane_surfaces
from backend.control_plane.console.state_views import (
    node_heartbeat_state,
    severity_view,
    tone_view,
)
from backend.kernel.contracts.status import normalize_persisted_status
from backend.kernel.execution.job_status import normalize_job_status
from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME, to_public_profile
from backend.models.connector import Connector
from backend.models.job import Job
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


_ATTEMPT_LOOKBACK_HOURS = 24


class ConsoleMenuItem(BaseModel):
    route_name: str
    route_path: str
    label: str
    endpoint: str
    enabled: bool
    requires_admin: bool = False
    reason: str | None = None


class ConsoleMenuResponse(BaseModel):
    product: str = Field(..., description="Frozen product definition")
    profile: str = Field(..., description="Public gateway profile")
    runtime_profile: str = Field(..., description="Internal backend runtime profile")
    items: list[ConsoleMenuItem] = Field(default_factory=list)


class ConsoleRouteTarget(BaseModel):
    route_path: str
    query: dict[str, str] = Field(default_factory=dict)


class ConsoleSummaryCard(BaseModel):
    key: str
    kicker: str
    title: str
    value: int
    badge: str
    detail: str
    tone: str
    tone_view: StatusView
    route: ConsoleRouteTarget | None = None


class OverviewBucket(BaseModel):
    total: int = 0
    active: int = 0
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    degraded: int = 0
    offline: int = 0
    rejected: int = 0
    attention: int = 0
    stale: int = 0
    high_priority_backlog: int = 0


class OverviewAttentionItem(BaseModel):
    severity: str
    severity_view: StatusView
    title: str
    count: int
    reason: str
    route: ConsoleRouteTarget


class ConsoleOverviewResponse(BaseModel):
    product: str
    profile: str
    runtime_profile: str
    nodes: OverviewBucket
    jobs: OverviewBucket
    connectors: OverviewBucket
    summary_cards: list[ConsoleSummaryCard] = Field(default_factory=list)
    attention: list[OverviewAttentionItem] = Field(default_factory=list)
    generated_at: datetime.datetime


class ConsoleDiagnosticsSegment(BaseModel):
    key: str
    label: str
    count: int
    route: ConsoleRouteTarget


class ConsoleNodeDiagnostic(BaseModel):
    node_id: str
    name: str
    node_type: str
    executor: str
    os: str
    arch: str
    zone: str | None
    status: str
    status_view: StatusView
    enrollment_status: str
    drain_status: str
    drain_status_view: StatusView
    heartbeat_state: str
    heartbeat_state_view: StatusView
    capacity_state: str
    capacity_state_view: StatusView
    active_lease_count: int
    max_concurrency: int
    cpu_cores: int
    memory_mb: int
    gpu_vram_mb: int
    storage_mb: int
    reliability_score: float
    health_reason: str | None
    attention_reason: str | None
    actions: list[ControlAction] = Field(default_factory=list)
    last_seen_at: datetime.datetime
    route: ConsoleRouteTarget


class ConsoleConnectorDiagnostic(BaseModel):
    connector_id: str
    name: str
    kind: str
    status: str
    status_view: StatusView
    profile: str
    endpoint: str | None
    last_test_status: str | None
    last_test_message: str | None
    last_invoke_status: str | None
    last_invoke_message: str | None
    attention_reason: str | None
    actions: list[ControlAction] = Field(default_factory=list)
    updated_at: datetime.datetime
    route: ConsoleRouteTarget


class ConsoleStaleJobDiagnostic(BaseModel):
    job_id: str
    kind: str
    node_id: str | None
    attempt: int
    priority: int
    source: str
    leased_until: datetime.datetime | None
    lease_state: str
    lease_state_view: StatusView
    attention_reason: str | None
    actions: list[ControlAction] = Field(default_factory=list)
    route: ConsoleRouteTarget


class ConsoleUnschedulableJobDiagnostic(BaseModel):
    job_id: str
    kind: str
    priority: int
    priority_view: StatusView
    source: str
    selectors: list[str] = Field(default_factory=list)
    blocker_summary: list[str] = Field(default_factory=list)
    created_at: datetime.datetime
    actions: list[ControlAction] = Field(default_factory=list)
    route: ConsoleRouteTarget


class ConsoleDiagnosticsResponse(BaseModel):
    product: str
    profile: str
    runtime_profile: str
    node_health: list[ConsoleNodeDiagnostic] = Field(default_factory=list)
    connector_health: list[ConsoleConnectorDiagnostic] = Field(default_factory=list)
    stale_jobs: list[ConsoleStaleJobDiagnostic] = Field(default_factory=list)
    unschedulable_jobs: list[ConsoleUnschedulableJobDiagnostic] = Field(default_factory=list)
    backlog_by_zone: list[ConsoleDiagnosticsSegment] = Field(default_factory=list)
    backlog_by_capability: list[ConsoleDiagnosticsSegment] = Field(default_factory=list)
    backlog_by_executor: list[ConsoleDiagnosticsSegment] = Field(default_factory=list)
    generated_at: datetime.datetime


class ControlPlaneSurfaceResponse(BaseModel):
    """Control-plane surface definition (backend is the single source of truth)."""

    capability_key: str
    route_name: str
    route_path: str
    label: str
    description: str
    endpoint: str
    backend_router: str
    frontend_view: str
    profiles: list[str]
    requires_admin: bool = False


class ControlPlaneSurfacesResponse(BaseModel):
    """All control-plane surfaces for the current profile."""

    product: str = Field(..., description="Frozen product definition")
    profile: str = Field(..., description="Public gateway profile")
    runtime_profile: str = Field(..., description="Internal backend runtime profile")
    surfaces: list[ControlPlaneSurfaceResponse] = Field(default_factory=list)


# Pure helper functions


def build_menu_response(runtime_profile: str, *, is_admin: bool) -> ConsoleMenuResponse:
    profile = to_public_profile(runtime_profile)
    items: list[ConsoleMenuItem] = []
    for surface in iter_control_plane_surfaces(runtime_profile, is_admin=is_admin):
        items.append(
            ConsoleMenuItem(
                route_name=surface.route_name,
                route_path=surface.route_path,
                label=surface.label,
                endpoint=surface.endpoint,
                enabled=True,
                requires_admin=surface.requires_admin,
                reason=surface.description,
            )
        )

    return ConsoleMenuResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=profile,
        runtime_profile=runtime_profile,
        items=items,
    )


def route_target(route_path: str, **query: str | int | None) -> ConsoleRouteTarget:
    return ConsoleRouteTarget(
        route_path=route_path,
        query={key: str(value) for key, value in query.items() if value is not None and str(value) != ""},
    )


def count_label(value: int, suffix: str) -> str:
    return f"{value} {suffix}"


def _node_detail_text(node_bucket: OverviewBucket) -> str:
    return f"{node_bucket.pending} pending enrollment, {node_bucket.rejected} rejected, {node_bucket.degraded} degraded, {node_bucket.offline} offline"


def _job_detail_text(job_bucket: OverviewBucket) -> str:
    return f"{job_bucket.running} running, {job_bucket.stale} stale leases"


def _connector_detail_text(connector_bucket: OverviewBucket) -> str:
    return f"{connector_bucket.pending} configured only, {connector_bucket.failed} hard errors"


def build_summary_cards(
    *,
    node_bucket: OverviewBucket,
    job_bucket: OverviewBucket,
    connector_bucket: OverviewBucket,
) -> list[ConsoleSummaryCard]:
    return [
        ConsoleSummaryCard(
            key="nodes",
            kicker="Nodes",
            title="Active runners serving work",
            value=node_bucket.active,
            badge=count_label(node_bucket.attention, "attention"),
            detail=_node_detail_text(node_bucket),
            tone="info",
            tone_view=StatusView(**tone_view("info")),
            route=route_target("/nodes", attention="attention"),
        ),
        ConsoleSummaryCard(
            key="jobs",
            kicker="Queue",
            title="Jobs waiting for pickup",
            value=job_bucket.pending,
            badge=count_label(job_bucket.high_priority_backlog, "high"),
            detail=_job_detail_text(job_bucket),
            tone="warning",
            tone_view=StatusView(**tone_view("warning")),
            route=route_target("/jobs", status="pending"),
        ),
        ConsoleSummaryCard(
            key="failures",
            kicker="Failures",
            title="Completed executions with failure",
            value=job_bucket.failed,
            badge=count_label(job_bucket.completed, "ok"),
            detail="Failures should route into retry, triage, or connector remediation.",
            tone="danger",
            tone_view=StatusView(**tone_view("danger")),
            route=route_target("/jobs", status="failed"),
        ),
        ConsoleSummaryCard(
            key="connectors",
            kicker="Connectors",
            title="Integrations needing care",
            value=connector_bucket.attention,
            badge=count_label(connector_bucket.active, "healthy"),
            detail=_connector_detail_text(connector_bucket),
            tone="success",
            tone_view=StatusView(**tone_view("success")),
            route=route_target("/connectors", attention="attention"),
        ),
    ]


def build_attention(
    *,
    node_bucket: OverviewBucket,
    job_bucket: OverviewBucket,
    connector_bucket: OverviewBucket,
) -> list[OverviewAttentionItem]:
    items: list[OverviewAttentionItem] = []
    if node_bucket.degraded or node_bucket.offline:
        items.append(
            OverviewAttentionItem(
                severity="critical",
                severity_view=StatusView(**severity_view("critical")),
                title="Node health requires attention",
                count=node_bucket.degraded + node_bucket.offline,
                reason="Active runners are stale or offline and may stop consuming work.",
                route=route_target("/nodes", attention="attention"),
            )
        )
    if node_bucket.pending:
        items.append(
            OverviewAttentionItem(
                severity="warning",
                severity_view=StatusView(**severity_view("warning")),
                title="Nodes waiting for enrollment",
                count=node_bucket.pending,
                reason="Provisioned runners have not completed register/heartbeat yet.",
                route=route_target("/nodes", enrollment_status="pending"),
            )
        )
    if node_bucket.rejected:
        items.append(
            OverviewAttentionItem(
                severity="warning",
                severity_view=StatusView(**severity_view("warning")),
                title="Nodes rejected by control plane",
                count=node_bucket.rejected,
                reason="Rejected runners must be reprovisioned before they can rejoin scheduling.",
                route=route_target("/nodes", enrollment_status="rejected"),
            )
        )
    if job_bucket.high_priority_backlog:
        items.append(
            OverviewAttentionItem(
                severity="critical",
                severity_view=StatusView(**severity_view("critical")),
                title="High-priority backlog detected",
                count=job_bucket.high_priority_backlog,
                reason="Priority 80+ jobs are waiting for an eligible runner.",
                route=route_target("/jobs", status="pending", priority_bucket="high"),
            )
        )
    if job_bucket.failed:
        items.append(
            OverviewAttentionItem(
                severity="warning",
                severity_view=StatusView(**severity_view("warning")),
                title="Jobs are failing",
                count=job_bucket.failed,
                reason="Completed execution ended in failure and needs retry or triage.",
                route=route_target("/jobs", status="failed"),
            )
        )
    if job_bucket.stale:
        items.append(
            OverviewAttentionItem(
                severity="critical",
                severity_view=StatusView(**severity_view("critical")),
                title="Leases expired without completion",
                count=job_bucket.stale,
                reason="Leased jobs missed their lease deadline and may require re-dispatch review.",
                route=route_target("/jobs", lease_state="stale"),
            )
        )
    if connector_bucket.attention:
        items.append(
            OverviewAttentionItem(
                severity="warning",
                severity_view=StatusView(**severity_view("warning")),
                title="Connectors are not healthy",
                count=connector_bucket.attention,
                reason="Configured integrations are not in an online/healthy state.",
                route=route_target("/connectors", attention="attention"),
            )
        )
    severity_weight = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda item: (severity_weight.get(item.severity, 9), -item.count, item.title))
    return items


def sorted_segments(
    counts: dict[str, int],
    *,
    any_label: str,
    route_path: str,
    query_key: str,
) -> list[ConsoleDiagnosticsSegment]:
    segments = [
        ConsoleDiagnosticsSegment(
            key=key,
            label=any_label if key == "*" else key,
            count=count,
            route=route_target(route_path, status="pending", **({} if key == "*" else {query_key: key})),
        )
        for key, count in counts.items()
        if count > 0
    ]
    segments.sort(key=lambda item: (-item.count, item.label))
    return segments[:8]


def selector_summary(job: Job) -> list[str]:
    selectors: list[str] = []
    if job.target_os:
        selectors.append(f"os={job.target_os}")
    if job.target_arch:
        selectors.append(f"arch={job.target_arch}")
    if job.target_executor:
        selectors.append(f"persona={job.target_executor}")
    if job.target_zone:
        selectors.append(f"zone={job.target_zone}")
    if job.required_capabilities:
        selectors.append(f"capabilities={','.join(job.required_capabilities)}")
    if job.required_cpu_cores:
        selectors.append(f"cpu>={job.required_cpu_cores}")
    if job.required_memory_mb:
        selectors.append(f"memory>={job.required_memory_mb}")
    if job.required_gpu_vram_mb:
        selectors.append(f"gpu>={job.required_gpu_vram_mb}")
    if job.required_storage_mb:
        selectors.append(f"storage>={job.required_storage_mb}")
    if not selectors:
        selectors.append("selectors=any")
    return selectors


def build_node_overview_bucket(nodes: list[Node], now: datetime.datetime) -> OverviewBucket:
    bucket = OverviewBucket(total=len(nodes))
    for node in nodes:
        enrollment_status = normalize_persisted_status("nodes.enrollment_status", node.enrollment_status) or "pending"
        drain_status = node.drain_status or "active"
        heartbeat_state = node_heartbeat_state(node.last_seen_at, now)
        if enrollment_status == "rejected":
            bucket.rejected += 1
        elif enrollment_status == "pending":
            bucket.pending += 1
        elif node.status != "online":
            bucket.offline += 1
        elif drain_status != "active" or heartbeat_state == "stale":
            bucket.degraded += 1
        else:
            bucket.active += 1
    bucket.attention = bucket.pending + bucket.degraded + bucket.offline + bucket.rejected
    return bucket


def build_job_overview_bucket(jobs: list[Job], now: datetime.datetime) -> OverviewBucket:
    bucket = OverviewBucket(total=len(jobs))
    for job in jobs:
        job_status = normalize_job_status(job.status) or str(job.status or "pending")
        if job_status == "pending":
            bucket.pending += 1
            if int(job.priority or 0) >= 80:
                bucket.high_priority_backlog += 1
            continue
        if job_status == "leased":
            if job.leased_until and job.leased_until < now:
                bucket.stale += 1
            else:
                bucket.running += 1
            continue
        if job_status == "completed":
            bucket.completed += 1
        elif job_status == "failed":
            bucket.failed += 1
        elif job_status == "cancelled":
            bucket.cancelled += 1
    bucket.attention = bucket.failed + bucket.stale + bucket.high_priority_backlog
    return bucket


def build_connector_overview_bucket(connectors: list[Connector]) -> OverviewBucket:
    bucket = OverviewBucket(total=len(connectors))
    for connector in connectors:
        if connector.status in {"healthy", "online"}:
            bucket.active += 1
        elif connector.status == "configured":
            bucket.pending += 1
        elif connector.status in {"error", "auth_required"}:
            bucket.failed += 1
        else:
            bucket.degraded += 1
    bucket.attention = bucket.pending + bucket.degraded + bucket.failed
    return bucket
