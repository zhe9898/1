from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

from sqlalchemy import Integer, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.kernel.scheduling.backfill_scheduling import get_reservation_manager
from backend.kernel.scheduling.failure_control_plane import get_failure_control_plane
from backend.kernel.scheduling.governance_facade import get_governance_facade
from backend.kernel.execution.job_concurrency_service import build_job_concurrency_window
from backend.kernel.execution.job_lifecycle_service import JobLifecycleService
from backend.kernel.execution.lease_service import LeaseGrant, LeaseService
from backend.kernel.policy.runtime_policy_resolver import get_runtime_policy_resolver
from backend.kernel.scheduling.job_scheduler import build_node_snapshot
from backend.kernel.scheduling.job_scheduler import select_jobs_for_node
from backend.kernel.scheduling.placement_grpc_client import async_build_time_budgeted_placement_plan
from backend.kernel.scheduling.reservation_runtime import choose_reservation_slot
from backend.kernel.topology.node_auth import authenticate_node_request
from backend.platform.redis.client import CHANNEL_JOB_EVENTS, CHANNEL_RESERVATION_EVENTS, RedisClient
from backend.platform.db.advisory_locks import acquire_transaction_advisory_locks
from backend.kernel.scheduling.scheduling_governance import (
    SCHED_FLAG_DECISION_AUDIT,
    SCHED_FLAG_EXECUTOR_VALIDATION,
    SCHED_FLAG_PLACEMENT_POLICIES,
    SCHED_FLAG_PREEMPTION,
    SchedulingDecisionLogger,
)
from backend.kernel.scheduling.scheduling_policy_service import SchedulingPolicyService
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt

from .database import (
    _append_log,
    _build_snapshots,
    _get_current_attempt,
    _load_node_metrics,
    _load_recent_failed_job_ids,
)
from .deadline_maintenance import maybe_schedule_deadline_dlq_sweep
from .helpers import _to_lease_response, _to_response, _utcnow
from .models import JobLeaseResponse, JobPullRequest

if TYPE_CHECKING:
    from backend.kernel.policy.types import DispatchConfig


@dataclass(frozen=True, slots=True)
class PullJobsDependencies:
    authenticate_node_request: Callable[..., Awaitable[Any]]
    acquire_transaction_advisory_locks: Callable[..., Awaitable[None]]
    get_reservation_manager: Callable[[], Any]
    get_governance_facade: Callable[[], Any]
    maybe_schedule_deadline_dlq_sweep: Callable[[str, RedisClient | None], None]
    get_failure_control_plane: Callable[[], Any]
    load_node_metrics: Callable[..., Awaitable[tuple[list[Any], dict[str, int], dict[str, float]]]]
    build_snapshots: Callable[..., list[Any]]
    build_job_concurrency_window: Callable[..., Any]
    load_recent_failed_job_ids: Callable[..., Awaitable[set[str]]]
    async_build_time_budgeted_placement_plan: Callable[..., Awaitable[Any]]
    select_jobs_for_node: Callable[..., list[Any]]
    append_log: Callable[..., Awaitable[None]]
    get_current_attempt: Callable[..., Awaitable[Any]]
    publish_control_event: Callable[..., Awaitable[None]]
    to_response: Callable[..., Any]
    to_lease_response: Callable[..., JobLeaseResponse]
    utcnow: Callable[[], Any]


def build_default_pull_jobs_dependencies() -> PullJobsDependencies:
    return PullJobsDependencies(
        authenticate_node_request=authenticate_node_request,
        acquire_transaction_advisory_locks=acquire_transaction_advisory_locks,
        get_reservation_manager=get_reservation_manager,
        get_governance_facade=get_governance_facade,
        maybe_schedule_deadline_dlq_sweep=maybe_schedule_deadline_dlq_sweep,
        get_failure_control_plane=get_failure_control_plane,
        load_node_metrics=_load_node_metrics,
        build_snapshots=_build_snapshots,
        build_job_concurrency_window=build_job_concurrency_window,
        load_recent_failed_job_ids=_load_recent_failed_job_ids,
        async_build_time_budgeted_placement_plan=async_build_time_budgeted_placement_plan,
        select_jobs_for_node=select_jobs_for_node,
        append_log=_append_log,
        get_current_attempt=_get_current_attempt,
        publish_control_event=publish_control_event,
        to_response=_to_response,
        to_lease_response=_to_lease_response,
        utcnow=_utcnow,
    )


def _get_dispatch_config() -> DispatchConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.dispatch


async def _publish_reservation_event(
    redis: RedisClient | None,
    action: str,
    reservation: object,
    *,
    publish_control_event: Callable[..., Awaitable[None]],
    reason: str | None = None,
    source: str = "dispatch",
) -> None:
    payload = {
        "reservation": getattr(reservation, "to_dict")(),
        "source": source,
    }
    if reason:
        payload["reason"] = reason
    await publish_control_event(redis, CHANNEL_RESERVATION_EVENTS, action, payload)


async def execute_pull_jobs(
    payload: JobPullRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    node_token: str,
    deps: PullJobsDependencies,
) -> list[JobLeaseResponse]:
    requesting_node = await deps.authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=True,
        tenant_id=payload.tenant_id,
    )
    await deps.acquire_transaction_advisory_locks(
        db,
        [
            ("jobs.pull.node", (payload.tenant_id, payload.node_id)),
        ],
    )

    now = deps.utcnow()
    reservation_mgr = deps.get_reservation_manager()
    expired_reservations = [r for r in reservation_mgr.list_reservations(tenant_id=payload.tenant_id) if r.is_expired(now)]
    if expired_reservations:
        reservation_mgr.cleanup_expired(now)
        for reservation in expired_reservations:
            await _publish_reservation_event(
                redis,
                "expired",
                reservation,
                publish_control_event=deps.publish_control_event,
                reason="window_elapsed",
            )

    governance = deps.get_governance_facade()
    admission = await governance.pre_dispatch_admission(
        db,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        now=now,
    )
    if not admission.admitted:
        return []

    deps.maybe_schedule_deadline_dlq_sweep(payload.tenant_id, redis)

    ff_audit = await governance.is_feature_enabled(db, SCHED_FLAG_DECISION_AUDIT)
    ff_placement = await governance.is_feature_enabled(db, SCHED_FLAG_PLACEMENT_POLICIES)
    ff_preemption = await governance.is_feature_enabled(db, SCHED_FLAG_PREEMPTION)
    ff_executor_val = await governance.is_feature_enabled(db, SCHED_FLAG_EXECUTOR_VALIDATION)

    audit: SchedulingDecisionLogger = governance.create_decision_logger(
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        now=now,
    )

    failure_control_plane = deps.get_failure_control_plane()

    active_nodes, active_lease_counts, reliability_map = await deps.load_node_metrics(
        db,
        tenant_id=payload.tenant_id,
        now=now,
        only_active_enrollment=True,
    )
    node_snapshot = build_node_snapshot(
        requesting_node,
        active_lease_count=active_lease_counts.get(payload.node_id, 0),
        reliability_score=reliability_map.get(payload.node_id, _get_dispatch_config().default_reliability_score),
    )
    active_node_snapshots = deps.build_snapshots(
        active_nodes,
        active_lease_counts=active_lease_counts,
        reliability_map=reliability_map,
    )

    accepted_kinds = set(payload.accepted_kinds)
    dispatch_config = _get_dispatch_config()
    candidate_limit = min(
        max(payload.limit * dispatch_config.candidate_multiplier, dispatch_config.candidate_min),
        dispatch_config.candidate_max,
    )

    burst_active = await failure_control_plane.is_in_burst(now=now)
    if burst_active:
        candidate_limit = max(candidate_limit // dispatch_config.burst_throttle_divisor, dispatch_config.burst_throttle_floor)
    audit.context["burst_active"] = burst_active
    audit.context["feature_flags"] = {
        "decision_audit": ff_audit,
        "placement_policies": ff_placement,
        "preemption": ff_preemption,
        "executor_validation": ff_executor_val,
    }

    runtime_policy_snapshot = get_runtime_policy_resolver().snapshot(
        profile=os.getenv("GATEWAY_PROFILE", "gateway-kernel"),
        raw_packs=os.getenv("GATEWAY_PACKS", ""),
    )
    concurrency_window = deps.build_job_concurrency_window(db=db, tenant_id=payload.tenant_id)
    tenant_policy = await SchedulingPolicyService.get(db, payload.tenant_id)
    audit.context["policy_snapshot"] = {
        "policy_version": runtime_policy_snapshot.policy_version,
        "quota_version": int(getattr(tenant_policy, "config_version", 0) or 0),
        "governance_version": runtime_policy_snapshot.policy_version,
        "profile": runtime_policy_snapshot.profile,
        "active_packs": list(runtime_policy_snapshot.active_packs),
    }

    base_where = [
        Job.tenant_id == payload.tenant_id,
        or_(
            (Job.status == "pending") & (or_(Job.retry_at.is_(None), Job.retry_at <= now)),
            (Job.status == "leased") & (Job.leased_until.is_not(None)) & (Job.leased_until < now),
        ),
        or_(Job.deadline_at.is_(None), Job.deadline_at > now),
    ]

    age_seconds = func.greatest(
        func.extract("epoch", literal(now) - Job.created_at),
        literal(0),
    )

    from backend.kernel.policy.policy_store import get_policy_store as get_policy_store

    queue_config = get_policy_store().active.queue
    layers = queue_config.priority_layers
    layer_muls = queue_config.layer_aging_multipliers
    sorted_layers = sorted(layers.items(), key=lambda kv: kv[1][0], reverse=True)
    case_whens = [(Job.priority >= lo, literal(float(layer_muls.get(name, 1.0)))) for name, (lo, _hi) in sorted_layers[:-1]]
    else_mul = float(layer_muls.get(sorted_layers[-1][0], 1.0)) if sorted_layers else 1.0
    layer_multiplier = case(*case_whens, else_=literal(else_mul))

    aging_interval = float(queue_config.aging.interval_seconds)
    aging_cap = float(queue_config.aging.max_bonus) * max(layer_muls.values(), default=1.0)
    aging_bonus = func.least(
        func.sqrt(age_seconds / literal(aging_interval)) * layer_multiplier,
        literal(aging_cap),
    )
    effective_priority = func.least(
        Job.priority + func.cast(aging_bonus, Integer),
        literal(100),
    )

    query = (
        select(Job)
        .where(*base_where)
        .with_for_update(skip_locked=True)
        .order_by(effective_priority.desc(), Job.created_at.asc())
        .limit(candidate_limit)
    )
    if accepted_kinds:
        query = query.where(Job.kind.in_(accepted_kinds))

    result = await db.execute(query)
    candidates = list(result.scalars().all())

    pre_backoff = len(candidates)
    candidates = [candidate for candidate in candidates if not governance.should_skip_backoff(candidate.job_id, now)]
    backoff_skipped = pre_backoff - len(candidates)
    if backoff_skipped:
        governance.record_backoff_skip_metric()

    from backend.kernel.scheduling.queue_stratification import sort_jobs_by_stratified_priority

    candidates = cast(list[Job], sort_jobs_by_stratified_priority(candidates, now=now, aging_enabled=True))

    filtered_candidates: list[Job] = []
    for candidate in candidates:
        kind = getattr(candidate, "kind", None) or ""
        if kind:
            circuit_state = await failure_control_plane.get_kind_circuit_state(kind, now=now)
            if circuit_state == "open":
                audit.record_rejection(candidate.job_id, f"kind_circuit_open:{kind}")
                continue
        if ff_executor_val and kind:
            executor_filter = governance.filter_by_executor_contract(requesting_node.executor, kind)
            if not executor_filter.compatible:
                audit.record_rejection(candidate.job_id, f"executor_kind_incompat:{executor_filter.reason}")
                continue
        filtered_candidates.append(candidate)
    candidates = filtered_candidates

    from backend.kernel.scheduling.business_scheduling import apply_business_filters

    all_dep_ids: set[str] = set()
    for candidate in candidates:
        all_dep_ids.update(candidate.depends_on or [])
    if all_dep_ids:
        dep_result = await db.execute(
            select(Job.job_id).where(
                Job.tenant_id == payload.tenant_id,
                Job.job_id.in_(all_dep_ids),
                Job.status == "completed",
            )
        )
        completed_dep_ids: set[str] = set(dep_result.scalars().all())
    else:
        completed_dep_ids = set()

    parent_ids = {candidate.parent_job_id for candidate in candidates if candidate.parent_job_id}
    if parent_ids:
        parent_result = await db.execute(select(Job).where(Job.tenant_id == payload.tenant_id, Job.job_id.in_(parent_ids)))
        parent_jobs = {job.job_id: job for job in parent_result.scalars().all()}
    else:
        parent_jobs = {}

    available_slots = max(node_snapshot.max_concurrency - node_snapshot.active_lease_count, 0)

    extra_ctx: dict[str, object] = {}
    leased_result = await db.execute(
        select(Job).where(
            Job.tenant_id == payload.tenant_id,
            Job.status == "leased",
        )
    )
    leased_jobs = list(leased_result.scalars().all())
    active_jobs_by_node: dict[str, list[Job]] = defaultdict(list)
    for leased_job in leased_jobs:
        leased_node_id = getattr(leased_job, "node_id", None)
        if leased_node_id:
            active_jobs_by_node[str(leased_node_id)].append(leased_job)
    try:
        from backend.kernel.scheduling.quota_aware_scheduling import (
            FairShareCalculator,
            ResourceUsage,
            build_quota_accounts,
        )

        quota_accounts = build_quota_accounts(leased_jobs)
        extra_ctx["_quota_accounts"] = quota_accounts

        cluster_totals = ResourceUsage()
        for account in quota_accounts.values():
            cluster_totals.cpu_cores += account.usage.cpu_cores
            cluster_totals.memory_mb += account.usage.memory_mb
            cluster_totals.gpu_vram_mb += account.usage.gpu_vram_mb
            cluster_totals.concurrent_jobs += account.usage.concurrent_jobs
        fair_ratios = FairShareCalculator.compute_fair_shares(
            quota_accounts,
            cluster_totals,
        )
        extra_ctx["_fair_share_ratios"] = fair_ratios
    except Exception:
        extra_ctx["_fair_share_ratios"] = {}

    candidates = apply_business_filters(
        candidates,
        completed_job_ids=completed_dep_ids,
        available_slots=available_slots,
        parent_jobs=parent_jobs,
        now=now,
        extra_context=extra_ctx,
    )

    recent_failed_job_ids = await deps.load_recent_failed_job_ids(
        db,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        job_ids=[job.job_id for job in candidates],
        now=now,
    )
    active_jobs_on_node = list(active_jobs_by_node.get(payload.node_id, []))

    audit.candidates_count = len(candidates)

    zone_load: dict[str, int] = defaultdict(int)
    for snapshot in active_node_snapshots:
        if snapshot.zone:
            zone_load[snapshot.zone] += snapshot.active_lease_count
    governance.configure_zone_context(dict(zone_load))

    dispatch_start = time.monotonic()

    from backend.kernel.scheduling.placement_policy import set_placement_enabled

    set_placement_enabled(ff_placement)
    solver_dispatch_context: dict[str, object] = {}
    placement_plan = await deps.async_build_time_budgeted_placement_plan(
        candidates,
        active_node_snapshots,
        now=now,
        accepted_kinds=accepted_kinds,
        recent_failed_job_ids=recent_failed_job_ids,
        active_jobs_by_node=active_jobs_by_node,
        decision_context=solver_dispatch_context,
    )
    audit.context["solver_dispatch"] = solver_dispatch_context

    selected = deps.select_jobs_for_node(
        candidates,
        node_snapshot,
        active_node_snapshots,
        now=now,
        accepted_kinds=accepted_kinds,
        recent_failed_job_ids=recent_failed_job_ids,
        active_jobs_on_node=active_jobs_on_node,
        limit=payload.limit,
        placement_plan=placement_plan,
    )
    if selected:
        await deps.acquire_transaction_advisory_locks(
            db,
            [("jobs.lease.job", (payload.tenant_id, scored.job.job_id)) for scored in selected],
        )

    if ff_preemption and not selected and available_slots <= 0 and candidates and active_jobs_on_node:
        from backend.kernel.scheduling.business_scheduling import find_preemption_candidates

        can_preempt, _budget_reason = governance.can_preempt(now)
        if not can_preempt:
            governance.record_preemption_budget_hit()
        else:
            preemption_pairs = find_preemption_candidates(
                candidates,
                active_jobs_on_node,
                now=now,
            )
            for urgent_job, victim_job, reason in preemption_pairs:
                victim_attempt = await deps.get_current_attempt(db, victim_job)
                await JobLifecycleService.preempt_job(
                    db,
                    job=victim_job,
                    attempt=victim_attempt,
                    reason=f"preempted by {urgent_job.job_id}: {reason}",
                    now=now,
                )
                await deps.append_log(
                    db,
                    victim_job.job_id,
                    f"preempted by {urgent_job.job_id}: {reason}",
                    tenant_id=victim_job.tenant_id,
                )
                audit.record_preemption(victim_job.job_id, urgent_job.job_id, reason)
                governance.record_preemption(now)

                node_snapshot = build_node_snapshot(
                    requesting_node,
                    active_lease_count=max(node_snapshot.active_lease_count - 1, 0),
                    reliability_score=reliability_map.get(payload.node_id, _get_dispatch_config().default_reliability_score),
                )
                selected = deps.select_jobs_for_node(
                    [urgent_job],
                    node_snapshot,
                    active_node_snapshots,
                    now=now,
                    accepted_kinds=accepted_kinds,
                    recent_failed_job_ids=recent_failed_job_ids,
                    active_jobs_on_node=[job for job in active_jobs_on_node if job.job_id != victim_job.job_id],
                    limit=1,
                    placement_plan=placement_plan,
                )
                break

    selected_ids = {scored.job.job_id for scored in selected}
    for candidate in candidates:
        if candidate.job_id not in selected_ids:
            governance.record_backoff_failure(candidate.job_id, now)

    lease_grants: list[LeaseGrant] = []
    acquired_locks: list[str] = []
    try:
        for scored in selected:
            job = scored.job
            lock_name = f"job_dispatch:{payload.tenant_id}:{job.job_id}"
            if redis is not None:
                lock_ok = await redis.locks.acquire(lock_name, ttl=10)
                if not lock_ok:
                    continue
                acquired_locks.append(lock_name)
            previous_attempt = await deps.get_current_attempt(db, job)
            if job.status == "leased" and job.leased_until and job.leased_until < now:
                await JobLifecycleService.expire_lease(
                    db,
                    job=job,
                    attempt=previous_attempt,
                    now=now,
                )
            concurrency_violation = await concurrency_window.check_capacity_for_job(job)
            if concurrency_violation is not None:
                audit.record_rejection(job.job_id, concurrency_violation.audit_reason())
                continue
            lease_grant = await LeaseService.grant_lease(
                db,
                job=job,
                node_id=payload.node_id,
                score=scored.score,
                now=now,
            )
            concurrency_window.note_lease_granted(job)
            await deps.append_log(
                db,
                job.job_id,
                (
                    f"job leased by {payload.node_id} attempt={lease_grant.attempt_no} "
                    f"score={scored.score} eligible_nodes={scored.eligible_nodes_count}"
                ),
                tenant_id=job.tenant_id,
            )
            existing_reservation = reservation_mgr.get_reservation(job.job_id)
            if existing_reservation is not None and reservation_mgr.cancel_reservation(job.job_id):
                await _publish_reservation_event(
                    redis,
                    "canceled",
                    existing_reservation,
                    publish_control_event=deps.publish_control_event,
                    reason="leased",
                )
            lease_grants.append(lease_grant)
            active_jobs_by_node.setdefault(payload.node_id, []).append(job)
            governance.record_backoff_success(job.job_id)
            audit.record_placement(
                job_id=job.job_id,
                score=scored.score,
                breakdown=scored.score_breakdown,
                eligible_nodes=scored.eligible_nodes_count,
            )

        leased_jobs = [grant.job for grant in lease_grants]
        leased_job_ids = {job.job_id for job in leased_jobs}
        for candidate in sorted(candidates, key=lambda item: (-int(item.priority or 0), item.created_at, item.job_id)):
            if candidate.job_id in leased_job_ids:
                continue
            if reservation_mgr.get_reservation(candidate.job_id) is not None:
                continue
            if int(candidate.priority or 0) < reservation_mgr.config.reservation_min_priority:
                continue
            slot = choose_reservation_slot(
                candidate,
                active_node_snapshots,
                active_jobs_by_node,
                now=now,
                accepted_kinds=None,
                reservation_mgr=reservation_mgr,
            )
            if slot is None:
                continue
            reservation_node, reservation_start_at, _reservation_end_at = slot
            created_reservation = reservation_mgr.create_reservation(
                candidate,
                reservation_node,
                start_at=reservation_start_at,
            )
            if created_reservation is None:
                continue
            await deps.append_log(
                db,
                candidate.job_id,
                (
                    f"reservation created on {reservation_node.node_id} "
                    f"start={created_reservation.start_at.isoformat()} end={created_reservation.end_at.isoformat()}"
                ),
                tenant_id=candidate.tenant_id,
            )
            await _publish_reservation_event(
                redis,
                "created",
                created_reservation,
                publish_control_event=deps.publish_control_event,
                reason="dispatch_backfill_plan",
            )

        dispatch_ms = (time.monotonic() - dispatch_start) * 1000
        for _ in leased_jobs:
            governance.record_placement_metric(dispatch_ms)
        if candidates and not leased_jobs:
            governance.record_rejection_metric("no_eligible_slot")

        decision = await governance.post_dispatch_audit(db, audit, enabled=ff_audit)
        decision_id = getattr(decision, "id", None)
        if decision_id is not None:
            for grant in lease_grants:
                await LeaseService.attach_scheduling_decision(
                    db,
                    attempt=grant.attempt,
                    scheduling_decision_id=int(decision_id),
                    now=now,
                )

        responses = [deps.to_lease_response(job, now=now) for job in leased_jobs]
        await db.commit()
        if responses:
            await deps.publish_control_event(
                redis,
                CHANNEL_JOB_EVENTS,
                "leased",
                {
                    "node_id": payload.node_id,
                    "jobs": [deps.to_response(job, now=now).model_dump(mode="json") for job in leased_jobs],
                },
            )
        return responses
    finally:
        if redis is not None:
            for lock_name in acquired_locks:
                await redis.locks.release(lock_name)
