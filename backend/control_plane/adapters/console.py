"""ZEN70 Console API - Dashboard overview, diagnostics, and surface endpoints.

Models and helper functions extracted to console_helpers.py for maintainability.
"""

from __future__ import annotations

import datetime
import os

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.connectors import _build_connector_actions, _connector_attention_reason
from backend.control_plane.adapters.console_helpers import (  # noqa: F401 -- Re-export for external consumers (test_console_overview.py)
    _ATTEMPT_LOOKBACK_HOURS,
    ConsoleConnectorDiagnostic,
    ConsoleDiagnosticsResponse,
    ConsoleMenuResponse,
    ConsoleNodeDiagnostic,
    ConsoleOverviewResponse,
    ConsoleStaleJobDiagnostic,
    ConsoleUnschedulableJobDiagnostic,
    ControlPlaneSurfaceResponse,
    ControlPlaneSurfacesResponse,
    OverviewBucket,
    _utcnow,
    build_attention,
    build_connector_overview_bucket,
    build_job_overview_bucket,
    build_menu_response,
    build_node_overview_bucket,
    build_summary_cards,
    route_target,
    selector_summary,
    sorted_segments,
)
from backend.control_plane.adapters.deps import get_current_user, get_current_user_optional, get_tenant_db
from backend.control_plane.adapters.jobs.helpers import _build_job_actions
from backend.control_plane.adapters.nodes import _build_node_actions
from backend.control_plane.adapters.ui_contracts import StatusView
from backend.control_plane.auth.access_policy import has_admin_role
from backend.control_plane.cache_headers import apply_identity_no_store_headers
from backend.control_plane.console.manifest_service import iter_control_plane_surfaces
from backend.control_plane.console.state_views import (
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
    tone_view,
)
from backend.kernel.contracts.tenant_claims import require_current_user_tenant_id
from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.models.connector import Connector
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt
from backend.models.node import Node
from backend.runtime.scheduling.job_scheduler import build_node_snapshot, count_eligible_nodes_for_job, node_blockers_for_job

router = APIRouter(prefix="/api/v1/console", tags=["console"])


@router.get("/surfaces", response_model=ControlPlaneSurfacesResponse)
async def get_control_plane_surfaces(
    response: Response,
    current_user: dict | None = Depends(get_current_user_optional),
) -> ControlPlaneSurfacesResponse:
    """Get control-plane surfaces (backend is the single source of truth).

    Frontend should fetch surfaces from this endpoint instead of reading
    frontend/src/config/controlPlaneSurfaces.json.
    """
    apply_identity_no_store_headers(response)
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    is_admin = has_admin_role(current_user)

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
    response: Response,
    current_user: dict | None = Depends(get_current_user_optional),
) -> ConsoleMenuResponse:
    apply_identity_no_store_headers(response)
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    is_admin = has_admin_role(current_user)
    return build_menu_response(runtime_profile, is_admin=is_admin)


@router.get("/overview", response_model=ConsoleOverviewResponse)
async def get_console_overview(
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> ConsoleOverviewResponse:
    tenant_id = require_current_user_tenant_id(current_user)
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    now = _utcnow()

    node_result = await db.execute(select(Node).where(Node.tenant_id == tenant_id))
    nodes = list(node_result.scalars().all())
    job_result = await db.execute(select(Job).where(Job.tenant_id == tenant_id))
    jobs = list(job_result.scalars().all())
    connector_result = await db.execute(select(Connector).where(Connector.tenant_id == tenant_id))
    connectors = list(connector_result.scalars().all())

    node_bucket = build_node_overview_bucket(nodes, now)
    job_bucket = build_job_overview_bucket(jobs, now)
    connector_bucket = build_connector_overview_bucket(connectors)

    return ConsoleOverviewResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        nodes=node_bucket,
        jobs=job_bucket,
        connectors=connector_bucket,
        summary_cards=build_summary_cards(
            node_bucket=node_bucket,
            job_bucket=job_bucket,
            connector_bucket=connector_bucket,
        ),
        attention=build_attention(
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
    tenant_id = require_current_user_tenant_id(current_user)
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
                route=route_target("/nodes", node_id=node.node_id),
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
                route=route_target("/connectors", connector_id=connector.connector_id),
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
            route=route_target("/jobs", job_id=job.job_id),
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
                selectors=selector_summary(job),
                blocker_summary=blocker_summary,
                created_at=job.created_at,
                actions=_build_job_actions(job, now=now),
                route=route_target("/jobs", job_id=job.job_id),
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
        backlog_by_zone=sorted_segments(
            backlog_by_zone_counts,
            any_label="any zone",
            route_path="/jobs",
            query_key="target_zone",
        ),
        backlog_by_capability=sorted_segments(
            backlog_by_capability_counts,
            any_label="no extra capability",
            route_path="/jobs",
            query_key="required_capability",
        ),
        backlog_by_executor=sorted_segments(
            backlog_by_executor_counts,
            any_label="any executor",
            route_path="/jobs",
            query_key="target_executor",
        ),
        generated_at=now,
    )
