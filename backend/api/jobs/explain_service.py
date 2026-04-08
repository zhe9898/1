from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.ui_contracts import StatusView
from backend.control_plane.console.state_views import (
    eligibility_view,
    node_drain_status_view,
    node_status_view,
)
from backend.kernel.scheduling.failure_control_plane import get_failure_control_plane
from backend.kernel.scheduling.scheduling_governance import get_all_scheduling_flags
from backend.kernel.scheduling.job_scheduler import (
    count_eligible_nodes_for_job,
    node_blockers_for_job,
    score_job_for_node,
)
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt

from .database import _build_snapshots, _get_job_by_id, _load_node_metrics
from .helpers import _to_response, _utcnow
from .models import (
    JobExplainDecisionResponse,
    JobExplainGovernanceContext,
    JobExplainResponse,
)


@dataclass(frozen=True, slots=True)
class ExplainJobDependencies:
    get_job_by_id: Callable[..., Awaitable[Job]]
    load_node_metrics: Callable[..., Awaitable[tuple[list[Any], dict[str, int], dict[str, float]]]]
    build_snapshots: Callable[..., list[Any]]
    to_response: Callable[..., Any]
    utcnow: Callable[[], Any]


def build_default_explain_job_dependencies() -> ExplainJobDependencies:
    return ExplainJobDependencies(
        get_job_by_id=_get_job_by_id,
        load_node_metrics=_load_node_metrics,
        build_snapshots=_build_snapshots,
        to_response=_to_response,
        utcnow=_utcnow,
    )


def _get_dispatch_lookback_hours() -> int:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.dispatch.attempt_lookback_hours


async def explain_job_details(
    id: str,
    *,
    current_user: dict[str, object],
    db: AsyncSession,
    deps: ExplainJobDependencies,
) -> JobExplainResponse:
    tenant_id = str(current_user.get("tenant_id") or "default")
    now = deps.utcnow()
    job = await deps.get_job_by_id(db, tenant_id, id)

    nodes, active_lease_counts, reliability_map = await deps.load_node_metrics(db, tenant_id=tenant_id, now=now)
    snapshots = deps.build_snapshots(
        nodes,
        active_lease_counts=active_lease_counts,
        reliability_map=reliability_map,
    )

    failed_nodes_result = await db.execute(
        select(JobAttempt.node_id)
        .where(
            JobAttempt.tenant_id == tenant_id,
            JobAttempt.job_id == job.job_id,
            JobAttempt.status == "failed",
            JobAttempt.created_at >= now - datetime.timedelta(hours=_get_dispatch_lookback_hours()),
        )
        .distinct()
    )
    recent_failed_node_ids = {str(node_id) for node_id in failed_nodes_result.scalars().all() if node_id}
    eligible_nodes = count_eligible_nodes_for_job(job, snapshots, now=now)
    total_active_nodes = sum(1 for snapshot in snapshots if snapshot.enrollment_status == "active")

    leased_rows = await db.execute(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.status == "leased",
        )
    )
    active_by_node: dict[str, list[Job]] = defaultdict(list)
    for leased in leased_rows.scalars().all():
        node_id = getattr(leased, "node_id", None)
        if node_id:
            active_by_node[str(node_id)].append(leased)

    decisions: list[JobExplainDecisionResponse] = []
    for snapshot in snapshots:
        reasons = node_blockers_for_job(job, snapshot, now=now)
        eligible = not reasons
        score: int | None = None
        if eligible:
            score, _breakdown = score_job_for_node(
                job,
                snapshot,
                now=now,
                total_active_nodes=total_active_nodes,
                eligible_nodes_count=eligible_nodes,
                recent_failed_job_ids={job.job_id} if snapshot.node_id in recent_failed_node_ids else set(),
                active_jobs_on_node=active_by_node.get(snapshot.node_id, []),
            )
        decisions.append(
            JobExplainDecisionResponse(
                node_id=snapshot.node_id,
                eligible=eligible,
                eligibility_view=StatusView(**eligibility_view(eligible)),
                score=score,
                reasons=reasons,
                active_lease_count=snapshot.active_lease_count,
                max_concurrency=snapshot.max_concurrency,
                executor=snapshot.executor,
                os=snapshot.os,
                arch=snapshot.arch,
                zone=snapshot.zone,
                cpu_cores=snapshot.cpu_cores,
                memory_mb=snapshot.memory_mb,
                gpu_vram_mb=snapshot.gpu_vram_mb,
                storage_mb=snapshot.storage_mb,
                drain_status=snapshot.drain_status,
                drain_status_view=StatusView(**node_drain_status_view(snapshot.drain_status)),
                reliability_score=round(snapshot.reliability_score, 4),
                status=snapshot.status,
                status_view=StatusView(**node_status_view(snapshot.status)),
                last_seen_at=snapshot.last_seen_at,
            )
        )
    decisions.sort(key=lambda item: (not item.eligible, -(item.score or -10_000), item.node_id))

    from backend.kernel.scheduling.queue_stratification import (
        get_aging_config,
        get_fair_scheduler,
        get_starvation_threshold_seconds,
    )

    failure_control_plane = get_failure_control_plane()
    job_kind = getattr(job, "kind", "") or ""
    kind_circuit = await failure_control_plane.get_kind_circuit_state(job_kind, now=now) if job_kind else None
    feature_flags = await get_all_scheduling_flags(db)

    fair_scheduler = get_fair_scheduler()
    tenant_quota = fair_scheduler.get_quota(tenant_id)

    placement_policy_name = "default"
    try:
        from backend.kernel.scheduling.placement_policy import get_placement_policy

        placement_policy = get_placement_policy()
        placement_policy_name = getattr(placement_policy, "name", "composite") or "composite"
    except Exception:
        placement_policy_name = "default"

    governance = JobExplainGovernanceContext(
        feature_flags=feature_flags,
        kind_circuit_state=kind_circuit,
        node_quarantine_count=len(failure_control_plane._quarantine_until),
        connector_cooling_count=len(getattr(failure_control_plane, "_connector_cooling_until", {})),
        burst_active=await failure_control_plane.is_in_burst(now=now),
        tenant_service_class=tenant_quota.service_class,
        tenant_max_jobs_per_round=tenant_quota.max_jobs_per_round,
        tenant_fair_share_weight=tenant_quota.weight,
        placement_policy=placement_policy_name,
        starvation_threshold_seconds=get_starvation_threshold_seconds(),
        aging_config=get_aging_config(),
    )

    return JobExplainResponse(
        job=deps.to_response(job, now=now),
        total_nodes=len(snapshots),
        eligible_nodes=eligible_nodes,
        selected_node_id=job.node_id,
        decisions=decisions,
        governance=governance,
    )

