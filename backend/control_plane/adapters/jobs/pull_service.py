from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.control_events import publish_control_event
from backend.kernel.policy.runtime_policy_resolver import get_runtime_policy_resolver
from backend.models.job import Job
from backend.platform.db.advisory_locks import acquire_transaction_advisory_locks
from backend.platform.redis.client import CHANNEL_JOB_EVENTS, CHANNEL_RESERVATION_EVENTS, RedisClient
from backend.runtime.execution.job_concurrency_service import build_job_concurrency_window
from backend.runtime.execution.job_lifecycle_service import JobLifecycleService
from backend.runtime.execution.lease_service import LeaseGrant, LeaseService
from backend.runtime.scheduling.backfill_scheduling import get_reservation_manager
from backend.runtime.scheduling.failure_control_plane import get_failure_control_plane
from backend.runtime.scheduling.governance_facade import get_governance_facade
from backend.runtime.scheduling.job_scheduler import build_node_snapshot, select_jobs_for_node
from backend.runtime.scheduling.placement_grpc_client import async_build_time_budgeted_placement_plan
from backend.runtime.scheduling.reservation_runtime import choose_reservation_slot
from backend.runtime.scheduling.scheduling_governance import (
    SCHED_FLAG_DECISION_AUDIT,
    SCHED_FLAG_EXECUTOR_VALIDATION,
    SCHED_FLAG_PLACEMENT_POLICIES,
    SCHED_FLAG_PREEMPTION,
    SchedulingDecisionLogger,
)
from backend.runtime.scheduling.scheduling_policy_service import SchedulingPolicyService
from backend.runtime.topology.node_auth import authenticate_node_request

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
from .pull_candidates import (
    _build_candidate_context,
    _build_quota_context,  # re-exported for tests
    _get_dispatch_config,
    _get_starvation_rescue_limit,  # re-exported for tests
)
from .pull_contracts import (
    PullFeatureFlags,
    PullJobsDependencies,
    PullRuntimeContext,
    PullSelectionContext,
)

if TYPE_CHECKING:
    from backend.kernel.policy.types import DispatchConfig


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


async def _publish_reservation_event(
    redis: RedisClient | None,
    action: str,
    reservation: object,
    *,
    publish_control_event: Any,
    reason: str | None = None,
    source: str = "dispatch",
) -> None:
    del redis

    payload = {
        "reservation": getattr(reservation, "to_dict")(),
        "source": source,
    }
    if reason:
        payload["reason"] = reason
    await publish_control_event(
        CHANNEL_RESERVATION_EVENTS,
        action,
        payload,
        tenant_id=getattr(reservation, "tenant_id", None),
    )


async def _cleanup_expired_reservations(
    reservation_mgr: Any,
    *,
    tenant_id: str,
    now: Any,
    redis: RedisClient | None,
    publish_control_event: Any,
) -> None:
    expired_reservations = [reservation for reservation in reservation_mgr.list_reservations(tenant_id=tenant_id) if reservation.is_expired(now)]
    if not expired_reservations:
        return
    reservation_mgr.cleanup_expired(now)
    for reservation in expired_reservations:
        await _publish_reservation_event(
            redis,
            "expired",
            reservation,
            publish_control_event=publish_control_event,
            reason="window_elapsed",
        )


async def _select_dispatch_jobs(
    candidates: list[Job],
    *,
    active_jobs_by_node: dict[str, list[Job]],
    governance: Any,
    ff_placement: bool,
    node_snapshot: Any,
    active_node_snapshots: list[Any],
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str],
    active_jobs_on_node: list[Job],
    payload: JobPullRequest,
    now: Any,
    deps: PullJobsDependencies,
) -> tuple[list[Any], Any, dict[str, object]]:
    zone_load: dict[str, int] = defaultdict(int)
    for snapshot in active_node_snapshots:
        if snapshot.zone:
            zone_load[snapshot.zone] += snapshot.active_lease_count
    governance.configure_zone_context(dict(zone_load))

    from backend.runtime.scheduling.placement_policy import set_placement_enabled

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
    return selected, placement_plan, solver_dispatch_context


async def _maybe_preempt_and_reselect(
    db: AsyncSession,
    selected: list[Any],
    *,
    ff_preemption: bool,
    available_slots: int,
    candidates: list[Job],
    active_jobs_on_node: list[Job],
    governance: Any,
    node_snapshot: Any,
    requesting_node: Any,
    reliability_score: float,
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str],
    active_node_snapshots: list[Any],
    placement_plan: Any,
    now: Any,
    deps: PullJobsDependencies,
    audit: SchedulingDecisionLogger,
) -> tuple[list[Any], Any, list[Job]]:
    if not (ff_preemption and not selected and available_slots <= 0 and candidates and active_jobs_on_node):
        return selected, node_snapshot, active_jobs_on_node

    from backend.runtime.scheduling.business_scheduling import find_preemption_candidates

    can_preempt, _budget_reason = governance.can_preempt(now)
    if not can_preempt:
        governance.record_preemption_budget_hit()
        return selected, node_snapshot, active_jobs_on_node

    for urgent_job, victim_job, reason in find_preemption_candidates(candidates, active_jobs_on_node, now=now):
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

        updated_node_snapshot = build_node_snapshot(
            requesting_node,
            active_lease_count=max(node_snapshot.active_lease_count - 1, 0),
            reliability_score=reliability_score,
        )
        remaining_active_jobs = [job for job in active_jobs_on_node if job.job_id != victim_job.job_id]
        reselection = deps.select_jobs_for_node(
            [urgent_job],
            updated_node_snapshot,
            active_node_snapshots,
            now=now,
            accepted_kinds=accepted_kinds,
            recent_failed_job_ids=recent_failed_job_ids,
            active_jobs_on_node=remaining_active_jobs,
            limit=1,
            placement_plan=placement_plan,
        )
        return reselection, updated_node_snapshot, remaining_active_jobs

    return selected, node_snapshot, active_jobs_on_node


def _record_backoff_failures(candidates: list[Job], selected: list[Any], *, governance: Any, now: Any) -> None:
    selected_ids = {scored.job.job_id for scored in selected}
    for candidate in candidates:
        if candidate.job_id not in selected_ids:
            governance.record_backoff_failure(candidate.job_id, now)


async def _grant_single_lease(
    scored: Any,
    *,
    db: AsyncSession,
    payload: JobPullRequest,
    redis: RedisClient | None,
    now: Any,
    concurrency_window: Any,
    reservation_mgr: Any,
    deps: PullJobsDependencies,
    governance: Any,
    audit: SchedulingDecisionLogger,
    active_jobs_by_node: dict[str, list[Job]],
) -> tuple[LeaseGrant | None, str | None]:
    job = scored.job
    lock_name: str | None = None
    if redis is not None:
        lock_name = f"job_dispatch:{payload.tenant_id}:{job.job_id}"
        lock_ok = await redis.locks.acquire(lock_name, ttl=10)
        if not lock_ok:
            return None, None
    try:
        previous_attempt = await deps.get_current_attempt(db, job)
        if job.status == "leased" and job.leased_until and job.leased_until < now:
            await JobLifecycleService.expire_lease(db, job=job, attempt=previous_attempt, now=now)

        concurrency_violation = await concurrency_window.check_capacity_for_job(job)
        if concurrency_violation is not None:
            audit.record_rejection(job.job_id, concurrency_violation.audit_reason())
            return None, lock_name

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
            (f"job leased by {payload.node_id} attempt={lease_grant.attempt_no} " f"score={scored.score} eligible_nodes={scored.eligible_nodes_count}"),
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

        active_jobs_by_node.setdefault(payload.node_id, []).append(job)
        governance.record_backoff_success(job.job_id)
        audit.record_placement(
            job_id=job.job_id,
            score=scored.score,
            breakdown=scored.score_breakdown,
            eligible_nodes=scored.eligible_nodes_count,
        )
        return lease_grant, lock_name
    except Exception:
        if redis is not None and lock_name is not None:
            await redis.locks.release(lock_name)
        raise


async def _lease_selected_jobs(
    selected: list[Any],
    *,
    db: AsyncSession,
    payload: JobPullRequest,
    redis: RedisClient | None,
    now: Any,
    concurrency_window: Any,
    reservation_mgr: Any,
    deps: PullJobsDependencies,
    governance: Any,
    audit: SchedulingDecisionLogger,
    active_jobs_by_node: dict[str, list[Job]],
) -> tuple[list[LeaseGrant], list[str]]:
    lease_grants: list[LeaseGrant] = []
    acquired_locks: list[str] = []
    for scored in selected:
        lease_grant, lock_name = await _grant_single_lease(
            scored,
            db=db,
            payload=payload,
            redis=redis,
            now=now,
            concurrency_window=concurrency_window,
            reservation_mgr=reservation_mgr,
            deps=deps,
            governance=governance,
            audit=audit,
            active_jobs_by_node=active_jobs_by_node,
        )
        if lock_name is not None:
            acquired_locks.append(lock_name)
        if lease_grant is not None:
            lease_grants.append(lease_grant)
    return lease_grants, acquired_locks


async def _create_dispatch_reservations(
    candidates: list[Job],
    *,
    leased_jobs: list[Job],
    reservation_mgr: Any,
    active_node_snapshots: list[Any],
    active_jobs_by_node: dict[str, list[Job]],
    deps: PullJobsDependencies,
    db: AsyncSession,
    redis: RedisClient | None,
    now: Any,
) -> None:
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
        created_reservation = reservation_mgr.create_reservation(candidate, reservation_node, start_at=reservation_start_at)
        if created_reservation is None:
            continue
        await deps.append_log(
            db,
            candidate.job_id,
            (
                f"reservation created on {reservation_node.node_id} "
                f"start={created_reservation.start_at.isoformat()} "
                f"end={created_reservation.end_at.isoformat()}"
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


async def _load_pull_feature_flags(db: AsyncSession, *, governance: Any) -> PullFeatureFlags:
    return PullFeatureFlags(
        decision_audit=await governance.is_feature_enabled(db, SCHED_FLAG_DECISION_AUDIT),
        placement_policies=await governance.is_feature_enabled(db, SCHED_FLAG_PLACEMENT_POLICIES),
        preemption=await governance.is_feature_enabled(db, SCHED_FLAG_PREEMPTION),
        executor_validation=await governance.is_feature_enabled(db, SCHED_FLAG_EXECUTOR_VALIDATION),
    )


def _compute_candidate_limit(
    payload: JobPullRequest,
    *,
    dispatch_config: DispatchConfig,
    burst_active: bool,
) -> int:
    candidate_limit = min(
        max(payload.limit * dispatch_config.candidate_multiplier, dispatch_config.candidate_min),
        dispatch_config.candidate_max,
    )
    if burst_active:
        return max(candidate_limit // dispatch_config.burst_throttle_divisor, dispatch_config.burst_throttle_floor)
    return candidate_limit


async def _build_pull_runtime_context(
    payload: JobPullRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    node_token: str,
    deps: PullJobsDependencies,
) -> PullRuntimeContext | None:
    requesting_node = await deps.authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=True,
        tenant_id=payload.tenant_id,
    )
    await deps.acquire_transaction_advisory_locks(
        db,
        [("jobs.pull.node", (payload.tenant_id, payload.node_id))],
    )

    now = deps.utcnow()
    reservation_mgr = deps.get_reservation_manager()
    await _cleanup_expired_reservations(
        reservation_mgr,
        tenant_id=payload.tenant_id,
        now=now,
        redis=redis,
        publish_control_event=deps.publish_control_event,
    )

    governance = deps.get_governance_facade()
    admission = await governance.pre_dispatch_admission(
        db,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        now=now,
    )
    if not admission.admitted:
        return None

    deps.maybe_schedule_deadline_dlq_sweep(payload.tenant_id, redis)
    feature_flags = await _load_pull_feature_flags(db, governance=governance)
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
    dispatch_config = _get_dispatch_config()
    reliability_score = reliability_map.get(payload.node_id, dispatch_config.default_reliability_score)
    node_snapshot = build_node_snapshot(
        requesting_node,
        active_lease_count=active_lease_counts.get(payload.node_id, 0),
        reliability_score=reliability_score,
    )
    active_node_snapshots = deps.build_snapshots(
        active_nodes,
        active_lease_counts=active_lease_counts,
        reliability_map=reliability_map,
    )

    burst_active = await failure_control_plane.is_in_burst(now=now)
    accepted_kinds = set(payload.accepted_kinds)
    candidate_limit = _compute_candidate_limit(
        payload,
        dispatch_config=dispatch_config,
        burst_active=burst_active,
    )
    audit.context["burst_active"] = burst_active
    audit.context["feature_flags"] = feature_flags.as_dict()

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

    return PullRuntimeContext(
        requesting_node=requesting_node,
        now=now,
        reservation_mgr=reservation_mgr,
        governance=governance,
        feature_flags=feature_flags,
        audit=audit,
        failure_control_plane=failure_control_plane,
        node_snapshot=node_snapshot,
        active_node_snapshots=active_node_snapshots,
        reliability_score=reliability_score,
        accepted_kinds=accepted_kinds,
        candidate_limit=candidate_limit,
        concurrency_window=concurrency_window,
    )


async def _build_pull_selection_context(
    payload: JobPullRequest,
    *,
    db: AsyncSession,
    runtime: PullRuntimeContext,
    deps: PullJobsDependencies,
) -> PullSelectionContext:
    candidate_context = await _build_candidate_context(
        db=db,
        payload=payload,
        now=runtime.now,
        node_snapshot=runtime.node_snapshot,
        governance=runtime.governance,
        failure_control_plane=runtime.failure_control_plane,
        ff_executor_val=runtime.feature_flags.executor_validation,
        accepted_kinds=runtime.accepted_kinds,
        candidate_limit=runtime.candidate_limit,
        active_node_snapshots=runtime.active_node_snapshots,
        audit=runtime.audit,
        deps=deps,
    )
    active_jobs_on_node = list(candidate_context.active_jobs_by_node.get(payload.node_id, []))

    selected, placement_plan, solver_dispatch_context = await _select_dispatch_jobs(
        candidate_context.candidates,
        active_jobs_by_node=candidate_context.active_jobs_by_node,
        governance=runtime.governance,
        ff_placement=runtime.feature_flags.placement_policies,
        node_snapshot=runtime.node_snapshot,
        active_node_snapshots=runtime.active_node_snapshots,
        accepted_kinds=runtime.accepted_kinds,
        recent_failed_job_ids=candidate_context.recent_failed_job_ids,
        active_jobs_on_node=active_jobs_on_node,
        payload=payload,
        now=runtime.now,
        deps=deps,
    )
    runtime.audit.context["solver_dispatch"] = solver_dispatch_context
    selected, node_snapshot, active_jobs_on_node = await _maybe_preempt_and_reselect(
        db,
        selected,
        ff_preemption=runtime.feature_flags.preemption,
        available_slots=candidate_context.available_slots,
        candidates=candidate_context.candidates,
        active_jobs_on_node=active_jobs_on_node,
        governance=runtime.governance,
        node_snapshot=runtime.node_snapshot,
        requesting_node=runtime.requesting_node,
        reliability_score=runtime.reliability_score,
        accepted_kinds=runtime.accepted_kinds,
        recent_failed_job_ids=candidate_context.recent_failed_job_ids,
        active_node_snapshots=runtime.active_node_snapshots,
        placement_plan=placement_plan,
        now=runtime.now,
        deps=deps,
        audit=runtime.audit,
    )
    runtime.node_snapshot = node_snapshot
    _record_backoff_failures(candidate_context.candidates, selected, governance=runtime.governance, now=runtime.now)

    return PullSelectionContext(
        candidates=candidate_context.candidates,
        active_jobs_by_node=candidate_context.active_jobs_by_node,
        active_jobs_on_node=active_jobs_on_node,
        recent_failed_job_ids=candidate_context.recent_failed_job_ids,
        available_slots=candidate_context.available_slots,
        selected=selected,
        placement_plan=placement_plan,
    )


async def _commit_pull_selection(
    payload: JobPullRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    runtime: PullRuntimeContext,
    selection: PullSelectionContext,
    dispatch_start: float,
    deps: PullJobsDependencies,
) -> list[JobLeaseResponse]:
    if selection.selected:
        await deps.acquire_transaction_advisory_locks(
            db,
            [("jobs.lease.job", (payload.tenant_id, scored.job.job_id)) for scored in selection.selected],
        )

    acquired_locks: list[str] = []
    try:
        selection.active_jobs_by_node[payload.node_id] = selection.active_jobs_on_node
        lease_grants, acquired_locks = await _lease_selected_jobs(
            selection.selected,
            db=db,
            payload=payload,
            redis=redis,
            now=runtime.now,
            concurrency_window=runtime.concurrency_window,
            reservation_mgr=runtime.reservation_mgr,
            deps=deps,
            governance=runtime.governance,
            audit=runtime.audit,
            active_jobs_by_node=selection.active_jobs_by_node,
        )
        leased_jobs = [grant.job for grant in lease_grants]
        await _create_dispatch_reservations(
            selection.candidates,
            leased_jobs=leased_jobs,
            reservation_mgr=runtime.reservation_mgr,
            active_node_snapshots=runtime.active_node_snapshots,
            active_jobs_by_node=selection.active_jobs_by_node,
            deps=deps,
            db=db,
            redis=redis,
            now=runtime.now,
        )

        dispatch_ms = (time.monotonic() - dispatch_start) * 1000
        for _ in leased_jobs:
            runtime.governance.record_placement_metric(dispatch_ms)
        if selection.candidates and not leased_jobs:
            runtime.governance.record_rejection_metric("no_eligible_slot")

        decision = await runtime.governance.post_dispatch_audit(
            db,
            runtime.audit,
            enabled=runtime.feature_flags.decision_audit,
        )
        decision_id = getattr(decision, "id", None)
        if decision_id is not None:
            for grant in lease_grants:
                await LeaseService.attach_scheduling_decision(
                    db,
                    attempt=grant.attempt,
                    scheduling_decision_id=int(decision_id),
                    now=runtime.now,
                )

        responses = [deps.to_lease_response(job, now=runtime.now) for job in leased_jobs]
        await db.commit()
        if responses:
            await deps.publish_control_event(
                CHANNEL_JOB_EVENTS,
                "leased",
                {
                    "node_id": payload.node_id,
                    "jobs": [deps.to_response(job, now=runtime.now).model_dump(mode="json") for job in leased_jobs],
                },
                tenant_id=payload.tenant_id,
            )
        return responses
    finally:
        if redis is not None:
            for lock_name in acquired_locks:
                await redis.locks.release(lock_name)


async def execute_pull_jobs(
    payload: JobPullRequest,
    *,
    db: AsyncSession,
    redis: RedisClient | None,
    node_token: str,
    deps: PullJobsDependencies,
) -> list[JobLeaseResponse]:
    runtime = await _build_pull_runtime_context(
        payload,
        db=db,
        redis=redis,
        node_token=node_token,
        deps=deps,
    )
    if runtime is None:
        return []

    dispatch_start = time.monotonic()
    selection = await _build_pull_selection_context(
        payload,
        db=db,
        runtime=runtime,
        deps=deps,
    )
    return await _commit_pull_selection(
        payload,
        db=db,
        redis=redis,
        runtime=runtime,
        selection=selection,
        dispatch_start=dispatch_start,
        deps=deps,
    )
