from __future__ import annotations

import datetime
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.action_contracts import ControlAction
from backend.api.connectors import _build_connector_actions, _connector_attention_reason
from backend.api.deps import get_current_user, get_current_user_optional, get_tenant_db
from backend.api.jobs.helpers import _build_job_actions
from backend.api.nodes import _build_node_actions
from backend.api.ui_contracts import StatusView
from backend.core.control_plane import iter_control_plane_surfaces
from backend.core.control_plane_state import (
    connector_status_view,
    job_attention_reason,
    job_lease_state,
    job_lease_state_view,
    node_attention_reason,
    node_capacity_state,
    node_capacity_state_view,
    node_drain_status_view,
    node_heartbeat_state,
    node_heartbeat_state_view,
    node_status_view,
    severity_view,
    tone_view,
)
from backend.core.gateway_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.core.job_scheduler import build_node_snapshot, count_eligible_nodes_for_job, node_blockers_for_job
from backend.models.connector import Connector
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.node import Node

router = APIRouter(prefix="/api/v1/console", tags=["console"])

_ATTEMPT_LOOKBACK_HOURS = 24


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


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
    canceled: int = 0
    degraded: int = 0
    offline: int = 0
    revoked: int = 0
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


def _build_menu_response(runtime_profile: str, *, is_admin: bool) -> ConsoleMenuResponse:
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


def _route(route_path: str, **query: str | int | None) -> ConsoleRouteTarget:
    return ConsoleRouteTarget(
        route_path=route_path,
        query={key: str(value) for key, value in query.items() if value is not None and str(value) != ""},
    )


def _count_label(value: int, suffix: str) -> str:
    return f"{value} {suffix}"


def _node_detail_text(node_bucket: OverviewBucket) -> str:
    return f"{node_bucket.pending} pending enrollment, {node_bucket.degraded} degraded, {node_bucket.offline} offline"


def _job_detail_text(job_bucket: OverviewBucket) -> str:
    return f"{job_bucket.running} running, {job_bucket.stale} stale leases"


def _connector_detail_text(connector_bucket: OverviewBucket) -> str:
    return f"{connector_bucket.pending} configured only, {connector_bucket.failed} hard errors"


def _build_summary_cards(
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
            badge=_count_label(node_bucket.attention, "attention"),
            detail=_node_detail_text(node_bucket),
            tone="info",
            tone_view=StatusView(**tone_view("info")),
            route=_route("/nodes", attention="attention"),
        ),
        ConsoleSummaryCard(
            key="jobs",
            kicker="Queue",
            title="Jobs waiting for pickup",
            value=job_bucket.pending,
            badge=_count_label(job_bucket.high_priority_backlog, "high"),
            detail=_job_detail_text(job_bucket),
            tone="warning",
            tone_view=StatusView(**tone_view("warning")),
            route=_route("/jobs", status="pending"),
        ),
        ConsoleSummaryCard(
            key="failures",
            kicker="Failures",
            title="Completed executions with failure",
            value=job_bucket.failed,
            badge=_count_label(job_bucket.completed, "ok"),
            detail="Failures should route into retry, triage, or connector remediation.",
            tone="danger",
            tone_view=StatusView(**tone_view("danger")),
            route=_route("/jobs", status="failed"),
        ),
        ConsoleSummaryCard(
            key="connectors",
            kicker="Connectors",
            title="Integrations needing care",
            value=connector_bucket.attention,
            badge=_count_label(connector_bucket.active, "healthy"),
            detail=_connector_detail_text(connector_bucket),
            tone="success",
            tone_view=StatusView(**tone_view("success")),
            route=_route("/connectors", attention="attention"),
        ),
    ]


def _build_attention(
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
                route=_route("/nodes", attention="attention"),
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
                route=_route("/nodes", enrollment_status="pending"),
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
                route=_route("/jobs", status="pending", priority_bucket="high"),
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
                route=_route("/jobs", status="failed"),
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
                route=_route("/jobs", lease_state="stale"),
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
                route=_route("/connectors", attention="attention"),
            )
        )
    severity_weight = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda item: (severity_weight.get(item.severity, 9), -item.count, item.title))
    return items


def _sorted_segments(
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
            route=_route(route_path, status="pending", **({} if key == "*" else {query_key: key})),
        )
        for key, count in counts.items()
        if count > 0
    ]
    segments.sort(key=lambda item: (-item.count, item.label))
    return segments[:8]


def _selector_summary(job: Job) -> list[str]:
    selectors: list[str] = []
    if job.target_os:
        selectors.append(f"os={job.target_os}")
    if job.target_arch:
        selectors.append(f"arch={job.target_arch}")
    if job.target_executor:
        selectors.append(f"executor={job.target_executor}")
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


def _build_node_overview_bucket(nodes: list[Node], now: datetime.datetime) -> OverviewBucket:
    bucket = OverviewBucket(total=len(nodes))
    for node in nodes:
        drain_status = node.drain_status or "active"
        heartbeat_state = node_heartbeat_state(node.last_seen_at, now)
        if node.enrollment_status == "revoked":
            bucket.revoked += 1
        elif node.enrollment_status == "pending":
            bucket.pending += 1
        elif node.status != "online":
            bucket.offline += 1
        elif drain_status != "active" or heartbeat_state == "stale":
            bucket.degraded += 1
        else:
            bucket.active += 1
    bucket.attention = bucket.pending + bucket.degraded + bucket.offline + bucket.revoked
    return bucket


def _build_job_overview_bucket(jobs: list[Job], now: datetime.datetime) -> OverviewBucket:
    bucket = OverviewBucket(total=len(jobs))
    for job in jobs:
        if job.status == "pending":
            bucket.pending += 1
            if int(job.priority or 0) >= 80:
                bucket.high_priority_backlog += 1
            continue
        if job.status == "leased":
            if job.leased_until and job.leased_until < now:
                bucket.stale += 1
            else:
                bucket.running += 1
            continue
        if job.status == "completed":
            bucket.completed += 1
        elif job.status == "failed":
            bucket.failed += 1
        elif job.status == "canceled":
            bucket.canceled += 1
    bucket.attention = bucket.failed + bucket.stale + bucket.high_priority_backlog
    return bucket


def _build_connector_overview_bucket(connectors: list[Connector]) -> OverviewBucket:
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


@router.get("/surfaces", response_model=ControlPlaneSurfacesResponse)
async def get_control_plane_surfaces(
    current_user: dict | None = Depends(get_current_user_optional),
) -> ControlPlaneSurfacesResponse:
    """Get control-plane surfaces (backend is the single source of truth).

    Frontend should fetch surfaces from this endpoint instead of reading
    frontend/src/config/controlPlaneSurfaces.json.
    """
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    is_admin = bool(current_user and current_user.get("role") == "admin")

    surfaces = [
        ControlPlaneSurfaceResponse(
            capability_key=surface.capability_key,
            route_name=surface.route_name,
            route_path=surface.route_path,
            label=surface.label,
            description=surface.description,
            endpoint=surface.endpoint,
            backend_router=surface.backend_router,
            frontend_view=surface.frontend_view,
            profiles=list(surface.profiles),
            requires_admin=surface.requires_admin,
        )
        for surface in iter_control_plane_surfaces(runtime_profile, is_admin=is_admin)
    ]

    return ControlPlaneSurfacesResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        surfaces=surfaces,
    )


@router.get("/menu", response_model=ConsoleMenuResponse)
async def get_console_menu(
    current_user: dict | None = Depends(get_current_user_optional),
) -> ConsoleMenuResponse:
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    is_admin = bool(current_user and current_user.get("role") == "admin")
    return _build_menu_response(runtime_profile, is_admin=is_admin)


@router.get("/overview", response_model=ConsoleOverviewResponse)
async def get_console_overview(
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> ConsoleOverviewResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    now = _utcnow()

    node_result = await db.execute(select(Node).where(Node.tenant_id == tenant_id))
    nodes = list(node_result.scalars().all())
    job_result = await db.execute(select(Job).where(Job.tenant_id == tenant_id))
    jobs = list(job_result.scalars().all())
    connector_result = await db.execute(select(Connector).where(Connector.tenant_id == tenant_id))
    connectors = list(connector_result.scalars().all())

    node_bucket = _build_node_overview_bucket(nodes, now)
    job_bucket = _build_job_overview_bucket(jobs, now)
    connector_bucket = _build_connector_overview_bucket(connectors)

    return ConsoleOverviewResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        nodes=node_bucket,
        jobs=job_bucket,
        connectors=connector_bucket,
        summary_cards=_build_summary_cards(
            node_bucket=node_bucket,
            job_bucket=job_bucket,
            connector_bucket=connector_bucket,
        ),
        attention=_build_attention(
            node_bucket=node_bucket,
            job_bucket=job_bucket,
            connector_bucket=connector_bucket,
        ),
        generated_at=now,
    )


@router.get("/diagnostics", response_model=ConsoleDiagnosticsResponse)
async def get_console_diagnostics(
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> ConsoleDiagnosticsResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    now = _utcnow()
    node_result = await db.execute(select(Node).where(Node.tenant_id == tenant_id))
    nodes = list(node_result.scalars().all())
    job_result = await db.execute(select(Job).where(Job.tenant_id == tenant_id))
    jobs = list(job_result.scalars().all())
    attempt_result = await db.execute(
        select(JobAttempt.node_id, JobAttempt.status).where(
            JobAttempt.tenant_id == tenant_id, JobAttempt.created_at >= now - datetime.timedelta(hours=_ATTEMPT_LOOKBACK_HOURS)
        )
    )
    attempt_rows = list(attempt_result.all())

    reliability_map: dict[str, float] = {}
    by_node: dict[str, list[str]] = {}
    for node_id, status in attempt_rows:
        if not node_id:
            continue
        by_node.setdefault(str(node_id), []).append(str(status))
    for node_id, statuses in by_node.items():
        reliability_map[node_id] = sum(1 for status in statuses if status == "completed") / len(statuses)

    active_lease_counts: dict[str, int] = {}
    for job in jobs:
        if job.status == "leased" and job.node_id and job.leased_until is not None and job.leased_until > now:
            active_lease_counts[job.node_id] = active_lease_counts.get(job.node_id, 0) + 1

    snapshots = [
        build_node_snapshot(
            node,
            active_lease_count=active_lease_counts.get(node.node_id, 0),
            reliability_score=reliability_map.get(node.node_id, 0.85),
        )
        for node in nodes
    ]

    node_health: list[ConsoleNodeDiagnostic] = []
    for node in nodes:
        active_lease_count = active_lease_counts.get(node.node_id, 0)
        max_concurrency = max(int(node.max_concurrency or 1), 1)
        drain_status = node.drain_status or "active"
        heartbeat_state = node_heartbeat_state(node.last_seen_at, now)
        capacity_state = node_capacity_state(active_lease_count, max_concurrency)
        attention_reason = node_attention_reason(
            enrollment_status=node.enrollment_status,
            status=node.status,
            drain_status=drain_status,
            heartbeat_state=heartbeat_state,
            capacity_state=capacity_state,
            health_reason=node.health_reason,
        )
        node_health.append(
            ConsoleNodeDiagnostic(
                node_id=node.node_id,
                name=node.name,
                node_type=node.node_type,
                executor=node.executor,
                os=node.os,
                arch=node.arch,
                zone=node.zone,
                status=node.status,
                status_view=StatusView(**node_status_view(node.status)),
                enrollment_status=node.enrollment_status,
                drain_status=drain_status,
                drain_status_view=StatusView(**node_drain_status_view(drain_status)),
                heartbeat_state=heartbeat_state,
                heartbeat_state_view=StatusView(**node_heartbeat_state_view(heartbeat_state)),
                capacity_state=capacity_state,
                capacity_state_view=StatusView(**node_capacity_state_view(capacity_state)),
                active_lease_count=active_lease_count,
                max_concurrency=max_concurrency,
                cpu_cores=max(int(node.cpu_cores or 0), 0),
                memory_mb=max(int(node.memory_mb or 0), 0),
                gpu_vram_mb=max(int(node.gpu_vram_mb or 0), 0),
                storage_mb=max(int(node.storage_mb or 0), 0),
                reliability_score=round(reliability_map.get(node.node_id, 0.85), 4),
                health_reason=node.health_reason,
                attention_reason=attention_reason,
                actions=_build_node_actions(node),
                last_seen_at=node.last_seen_at,
                route=_route("/nodes", node_id=node.node_id),
            )
        )
    node_health.sort(
        key=lambda item: (
            item.attention_reason is None,
            item.heartbeat_state == "fresh",
            item.capacity_state == "available",
            -item.reliability_score,
            item.node_id,
        )
    )

    connector_health: list[ConsoleConnectorDiagnostic] = []
    connector_result = await db.execute(select(Connector).where(Connector.tenant_id == tenant_id))
    connectors = list(connector_result.scalars().all())
    for connector in connectors:
        attention_reason = _connector_attention_reason(connector)
        if attention_reason is None and connector.status in {"healthy", "online"}:
            continue
        connector_health.append(
            ConsoleConnectorDiagnostic(
                connector_id=connector.connector_id,
                name=connector.name,
                kind=connector.kind,
                status=connector.status,
                status_view=StatusView(**connector_status_view(connector.status)),
                profile=connector.profile,
                endpoint=connector.endpoint,
                last_test_status=connector.last_test_status,
                last_test_message=connector.last_test_message,
                last_invoke_status=connector.last_invoke_status,
                last_invoke_message=connector.last_invoke_message,
                attention_reason=attention_reason,
                actions=_build_connector_actions(connector),
                updated_at=connector.updated_at,
                route=_route("/connectors", connector_id=connector.connector_id),
            )
        )
    connector_health.sort(key=lambda item: (item.attention_reason is None, item.status, item.connector_id))

    pending_jobs = [job for job in jobs if job.status == "pending"]
    stale_jobs = [
        ConsoleStaleJobDiagnostic(
            job_id=job.job_id,
            kind=job.kind,
            node_id=job.node_id,
            attempt=job.attempt,
            priority=int(job.priority or 0),
            source=job.source,
            leased_until=job.leased_until,
            lease_state=job_lease_state(status=job.status, leased_until=job.leased_until, now=now),
            lease_state_view=StatusView(**job_lease_state_view(job_lease_state(status=job.status, leased_until=job.leased_until, now=now))),
            attention_reason=job_attention_reason(
                status=job.status,
                priority=int(job.priority or 0),
                leased_until=job.leased_until,
                now=now,
            ),
            actions=_build_job_actions(job, now=now),
            route=_route("/jobs", job_id=job.job_id),
        )
        for job in jobs
        if job.status == "leased" and job.leased_until is not None and job.leased_until < now
    ]
    stale_jobs.sort(key=lambda item: (item.leased_until or now, -item.priority, item.job_id))

    backlog_by_zone_counts: dict[str, int] = {}
    backlog_by_capability_counts: dict[str, int] = {}
    backlog_by_executor_counts: dict[str, int] = {}
    unschedulable_jobs: list[ConsoleUnschedulableJobDiagnostic] = []
    for job in sorted(pending_jobs, key=lambda item: (-int(item.priority or 0), item.created_at))[:50]:
        backlog_by_zone_counts[job.target_zone or "*"] = backlog_by_zone_counts.get(job.target_zone or "*", 0) + 1
        backlog_by_executor_counts[job.target_executor or "*"] = backlog_by_executor_counts.get(job.target_executor or "*", 0) + 1
        capability_keys = list(job.required_capabilities or []) or ["*"]
        for capability in capability_keys:
            backlog_by_capability_counts[capability] = backlog_by_capability_counts.get(capability, 0) + 1

        eligible_count = count_eligible_nodes_for_job(job, snapshots, now=now)
        if eligible_count > 0:
            continue
        blocker_counts: dict[str, int] = {}
        for snapshot in snapshots:
            for blocker in node_blockers_for_job(job, snapshot, now=now):
                blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        blocker_summary = [key for key, _ in sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))[:3]]
        unschedulable_jobs.append(
            ConsoleUnschedulableJobDiagnostic(
                job_id=job.job_id,
                kind=job.kind,
                priority=int(job.priority or 0),
                priority_view=StatusView(**tone_view("danger" if int(job.priority or 0) >= 80 else "warning")),
                source=job.source,
                selectors=_selector_summary(job),
                blocker_summary=blocker_summary,
                created_at=job.created_at,
                actions=_build_job_actions(job, now=now),
                route=_route("/jobs", job_id=job.job_id),
            )
        )

    return ConsoleDiagnosticsResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        node_health=node_health[:8],
        connector_health=connector_health[:8],
        stale_jobs=stale_jobs[:8],
        unschedulable_jobs=unschedulable_jobs[:8],
        backlog_by_zone=_sorted_segments(
            backlog_by_zone_counts,
            any_label="any zone",
            route_path="/jobs",
            query_key="target_zone",
        ),
        backlog_by_capability=_sorted_segments(
            backlog_by_capability_counts,
            any_label="no extra capability",
            route_path="/jobs",
            query_key="required_capability",
        ),
        backlog_by_executor=_sorted_segments(
            backlog_by_executor_counts,
            any_label="any executor",
            route_path="/jobs",
            query_key="target_executor",
        ),
        generated_at=now,
    )
