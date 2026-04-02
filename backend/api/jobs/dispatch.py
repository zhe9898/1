"""
ZEN70 Jobs API – Dispatch endpoints (pull_jobs, explain_job).

Split from routes.py for maintainability. Contains the scheduling-heavy
pull and explain endpoints that orchestrate queue stratification,
business scheduling filters, and the scoring pipeline.
"""

from __future__ import annotations

import datetime
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.control_events import publish_control_event
from backend.api.deps import (
    get_current_user,
    get_machine_tenant_db,
    get_node_machine_token,
    get_redis,
    get_tenant_db,
)
from backend.api.ui_contracts import StatusView
from backend.core.backfill_scheduling import get_reservation_manager
from backend.core.control_plane_state import (
    eligibility_view,
    node_drain_status_view,
    node_status_view,
)
from backend.core.failure_control_plane import get_failure_control_plane
from backend.core.governance_facade import get_governance_facade
from backend.core.job_scheduler import (
    build_node_snapshot,
    build_time_budgeted_placement_plan,
    count_eligible_nodes_for_job,
    node_blockers_for_job,
    score_job_for_node,
    select_jobs_for_node,
)
from backend.core.node_auth import authenticate_node_request
from backend.core.redis_client import CHANNEL_JOB_EVENTS, CHANNEL_RESERVATION_EVENTS, RedisClient
from backend.core.reservation_runtime import choose_reservation_slot
from backend.core.scheduling_governance import (
    SCHED_FLAG_DECISION_AUDIT,
    SCHED_FLAG_EXECUTOR_VALIDATION,
    SCHED_FLAG_PLACEMENT_POLICIES,
    SCHED_FLAG_PREEMPTION,
    SchedulingDecisionLogger,
)
from backend.models.job import Job
from backend.models.job_attempt import JobAttempt

from .database import (
    _append_log,
    _build_snapshots,
    _create_attempt,
    _expire_previous_attempt_if_needed,
    _load_node_metrics,
    _load_recent_failed_job_ids,
    move_to_dead_letter_queue,
)
from .helpers import (
    _new_lease_token,
    _to_lease_response,
    _to_response,
    _utcnow,
)
from .models import (
    JobExplainDecisionResponse,
    JobExplainGovernanceContext,
    JobExplainResponse,
    JobLeaseResponse,
    JobPullRequest,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

if TYPE_CHECKING:
    from backend.core.scheduling_policy_types import DispatchConfig


def _get_dispatch_config() -> DispatchConfig:
    from backend.core.scheduling_policy_store import get_policy_store

    return get_policy_store().active.dispatch


async def _publish_reservation_event(
    redis: RedisClient | None,
    action: str,
    reservation: object,
    *,
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


@router.post("/pull", response_model=list[JobLeaseResponse])
async def pull_jobs(  # noqa: C901
    payload: JobPullRequest,
    db: AsyncSession = Depends(get_machine_tenant_db),
    redis: RedisClient | None = Depends(get_redis),
    node_token: str = Depends(get_node_machine_token),
) -> list[JobLeaseResponse]:
    requesting_node = await authenticate_node_request(
        db,
        payload.node_id,
        node_token,
        require_active=True,
        tenant_id=payload.tenant_id,
    )
    now = _utcnow()
    _reservation_mgr = get_reservation_manager()
    _expired_reservations = [r for r in _reservation_mgr.list_reservations(tenant_id=payload.tenant_id) if r.is_expired(now)]
    if _expired_reservations:
        _reservation_mgr.cleanup_expired(now)
        for reservation in _expired_reservations:
            await _publish_reservation_event(redis, "expired", reservation, reason="window_elapsed")
    _governance = get_governance_facade()

    # ── Governance pre-dispatch admission ────────────────────────────
    _admission = await _governance.pre_dispatch_admission(
        db,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        now=now,
    )
    if not _admission.admitted:
        return []

    # ── Feature flag snapshot (governance facade) ──────────────────
    _ff_audit = await _governance.is_feature_enabled(db, SCHED_FLAG_DECISION_AUDIT)
    _ff_placement = await _governance.is_feature_enabled(db, SCHED_FLAG_PLACEMENT_POLICIES)
    _ff_preemption = await _governance.is_feature_enabled(db, SCHED_FLAG_PREEMPTION)
    _ff_executor_val = await _governance.is_feature_enabled(db, SCHED_FLAG_EXECUTOR_VALIDATION)

    # ── Decision audit logger (governance facade) ────────────────────
    _audit: SchedulingDecisionLogger = _governance.create_decision_logger(  # type: ignore[assignment]
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        now=now,
    )

    # ── Quarantine gate (also checked by facade above, kept for fcp ref) ─
    _fcp = get_failure_control_plane()

    active_nodes, active_lease_counts, reliability_map = await _load_node_metrics(
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
    active_node_snapshots = _build_snapshots(
        active_nodes,
        active_lease_counts=active_lease_counts,
        reliability_map=reliability_map,
    )

    accepted_kinds = set(payload.accepted_kinds)
    _dc = _get_dispatch_config()
    candidate_limit = min(max(payload.limit * _dc.candidate_multiplier, _dc.candidate_min), _dc.candidate_max)

    # ── Burst throttle ───────────────────────────────────────────────
    # When the failure control plane detects a burst of failures, halve
    # the candidate window so fewer jobs are dispatched per pull cycle.
    # This reduces the blast radius while nodes/connectors stabilise.
    _burst_active = _fcp.is_in_burst(now=now)
    if _burst_active:
        candidate_limit = max(candidate_limit // _dc.burst_throttle_divisor, _dc.burst_throttle_floor)
    _audit.context["burst_active"] = _burst_active
    _audit.context["feature_flags"] = {
        "decision_audit": _ff_audit,
        "placement_policies": _ff_placement,
        "preemption": _ff_preemption,
        "executor_validation": _ff_executor_val,
    }

    _base_where = [
        Job.tenant_id == payload.tenant_id,
        or_(
            (Job.status == "pending") & (or_(Job.retry_at.is_(None), Job.retry_at <= now)),
            (Job.status == "leased") & (Job.leased_until.is_not(None)) & (Job.leased_until < now),
        ),
        # Push deadline filter into SQL so expired rows never leave the DB.
        or_(Job.deadline_at.is_(None), Job.deadline_at > now),
    ]

    # ── SQL-level effective priority with aging ──────────────────────
    # Compute age-adjusted priority *inside* the query so the ORDER BY
    # naturally promotes old low-priority jobs. This makes aging truly
    # global — the DB window already respects wait-time, so there is no
    # second-pass "anti-starvation sweep" needed.
    #
    # Formula: effective_priority = priority + min(sqrt(age_s / interval) * layer_mul, cap)
    #   Parameters and layer multipliers are read from the policy store.
    # This mirrors queue_stratification.calculate_effective_priority().

    _age_seconds = func.greatest(
        func.extract("epoch", literal(now) - Job.created_at),
        literal(0),
    )

    from backend.core.scheduling_policy_store import get_policy_store as _gps

    _qcfg = _gps().active.queue
    _layers = _qcfg.priority_layers
    _layer_muls = _qcfg.layer_aging_multipliers

    # Build SQL CASE branches dynamically from policy-store layer definitions.
    # Sort layers descending by lower-bound so the first match wins.
    _sorted_layers = sorted(_layers.items(), key=lambda kv: kv[1][0], reverse=True)
    _case_whens = [(Job.priority >= lo, literal(float(_layer_muls.get(name, 1.0)))) for name, (lo, _hi) in _sorted_layers[:-1]]
    _else_mul = float(_layer_muls.get(_sorted_layers[-1][0], 1.0)) if _sorted_layers else 1.0
    _layer_multiplier = case(*_case_whens, else_=literal(_else_mul))

    _aging_interval = float(_qcfg.aging.interval_seconds)
    _aging_cap = float(_qcfg.aging.max_bonus) * max(_layer_muls.values(), default=1.0)
    _aging_bonus = func.least(
        func.sqrt(_age_seconds / literal(_aging_interval)) * _layer_multiplier,
        literal(_aging_cap),
    )

    _effective_priority = func.least(
        Job.priority + func.cast(_aging_bonus, Integer),
        literal(100),
    )

    query = select(Job).where(*_base_where).with_for_update(skip_locked=True).order_by(_effective_priority.desc(), Job.created_at.asc()).limit(candidate_limit)
    if accepted_kinds:
        query = query.where(Job.kind.in_(accepted_kinds))

    result = await db.execute(query)
    candidates = list(result.scalars().all())

    # ── Scheduling backoff filter (skip unschedulable jobs in backoff) ─
    _pre_backoff = len(candidates)
    candidates = [c for c in candidates if not _governance.should_skip_backoff(c.job_id, now)]
    _backoff_skipped = _pre_backoff - len(candidates)
    if _backoff_skipped:
        _governance.record_backoff_skip_metric()

    # Apply Python-side stratification sort (re-classifies layers using
    # effective priority) for deterministic ordering with tiebreakers.
    from backend.core.queue_stratification import sort_jobs_by_stratified_priority

    candidates = sort_jobs_by_stratified_priority(candidates, now=now, aging_enabled=True)  # type: ignore[assignment, arg-type]

    # ── Connector cooling & kind circuit breaker gate ────────────────
    # Beyond node quarantine (checked above), also honour kind-level
    # circuit breakers so jobs whose kind is failing system-wide are
    # not dispatched until the circuit transitions to half-open/closed.
    _filtered_candidates: list[Job] = []
    for c in candidates:
        kind = getattr(c, "kind", None) or ""
        if kind:
            circuit_state = await _fcp.get_kind_circuit_state(kind, now=now)
            if circuit_state == "open":
                _audit.record_rejection(c.job_id, f"kind_circuit_open:{kind}")
                continue  # kind circuit open → skip until half-open
        # Executor contract kind-compat pre-check (governance facade)
        if _ff_executor_val and kind:
            _ef = _governance.filter_by_executor_contract(
                requesting_node.executor,
                kind,
            )
            if not _ef.compatible:
                _audit.record_rejection(c.job_id, f"executor_kind_incompat:{_ef.reason}")
                continue
        _filtered_candidates.append(c)
    candidates = _filtered_candidates

    # ── Phase 2: Business scheduling filters (single entry point) ────
    from backend.core.business_scheduling import apply_business_filters

    # Pre-fetch dependency and parent data needed by the filter
    all_dep_ids: set[str] = set()
    for c in candidates:
        all_dep_ids.update(c.depends_on or [])
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

    parent_ids = {c.parent_job_id for c in candidates if c.parent_job_id}
    if parent_ids:
        parent_result = await db.execute(select(Job).where(Job.tenant_id == payload.tenant_id, Job.job_id.in_(parent_ids)))
        parent_jobs = {j.job_id: j for j in parent_result.scalars().all()}
    else:
        parent_jobs = {}

    available_slots = max(node_snapshot.max_concurrency - node_snapshot.active_lease_count, 0)

    # ── Quota-aware fair-share data (injected into constraint context) ─
    _extra_ctx: dict[str, object] = {}
    _leased_result = await db.execute(
        select(Job).where(
            Job.tenant_id == payload.tenant_id,
            Job.status == "leased",
        )
    )
    _leased_jobs = list(_leased_result.scalars().all())
    _active_jobs_by_node: dict[str, list[Job]] = defaultdict(list)
    for leased_job in _leased_jobs:
        leased_node_id = getattr(leased_job, "node_id", None)
        if leased_node_id:
            _active_jobs_by_node[str(leased_node_id)].append(leased_job)
    try:
        from backend.core.quota_aware_scheduling import (
            FairShareCalculator,
            ResourceUsage,
            build_quota_accounts,
        )

        # Build per-tenant resource usage from all leased jobs in this tenant
        _quota_accounts = build_quota_accounts(_leased_jobs)
        _extra_ctx["_quota_accounts"] = _quota_accounts

        # Compute cluster totals for fair-share ratios
        _cluster_totals = ResourceUsage()
        for acct in _quota_accounts.values():
            _cluster_totals.cpu_cores += acct.usage.cpu_cores
            _cluster_totals.memory_mb += acct.usage.memory_mb
            _cluster_totals.gpu_vram_mb += acct.usage.gpu_vram_mb
            _cluster_totals.concurrent_jobs += acct.usage.concurrent_jobs
        _fair_ratios = FairShareCalculator.compute_fair_shares(
            _quota_accounts,
            _cluster_totals,
        )
        _extra_ctx["_fair_share_ratios"] = _fair_ratios
    except Exception:
        pass  # quota_aware_scheduling not available — skip gracefully

    candidates = apply_business_filters(
        candidates,
        completed_job_ids=completed_dep_ids,
        available_slots=available_slots,
        parent_jobs=parent_jobs,
        now=now,
        extra_context=_extra_ctx,
    )
    # ── End Phase 2 ──────────────────────────────────────────────────

    # ── Phase 3: Score, select, lease ────────────────────────────────
    recent_failed_job_ids = await _load_recent_failed_job_ids(
        db,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        job_ids=[job.job_id for job in candidates],
        now=now,
    )

    # Reuse the tenant-wide leased snapshot to avoid a second node-local query.
    active_jobs_on_node = list(_active_jobs_by_node.get(payload.node_id, []))

    _audit.candidates_count = len(candidates)

    # ── Topology spread zone context (governance facade) ────────────
    _zone_load: dict[str, int] = defaultdict(int)
    for _snap in active_node_snapshots:
        if _snap.zone:
            _zone_load[_snap.zone] += _snap.active_lease_count
    _governance.configure_zone_context(dict(_zone_load))

    _dispatch_start = time.monotonic()

    # Toggle placement policies per feature flag
    from backend.core.placement_policy import set_placement_enabled

    set_placement_enabled(_ff_placement)
    _solver_dispatch_context: dict[str, object] = {}
    placement_plan = build_time_budgeted_placement_plan(
        candidates,
        active_node_snapshots,
        now=now,
        accepted_kinds=accepted_kinds,
        recent_failed_job_ids=recent_failed_job_ids,
        active_jobs_by_node=_active_jobs_by_node,
        decision_context=_solver_dispatch_context,
    )
    _audit.context["solver_dispatch"] = _solver_dispatch_context

    selected = select_jobs_for_node(
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

    # ── Phase 4: Preemption enforcement ──────────────────────────────
    # When the node is at capacity (no slots) AND there are high-priority
    # candidates that were eligible but couldn't be placed, evaluate
    # preemption of currently-running low-priority jobs.
    # Gated by feature flag — allows disabling preemption globally.
    if _ff_preemption and not selected and available_slots <= 0 and candidates and active_jobs_on_node:
        from backend.core.business_scheduling import find_preemption_candidates

        _can_preempt, _budget_reason = _governance.can_preempt(now)
        if not _can_preempt:
            _governance.record_preemption_budget_hit()
        else:
            preemption_pairs = find_preemption_candidates(
                candidates,
                active_jobs_on_node,
                now=now,
            )
            for urgent_job, victim_job, reason in preemption_pairs:
                # Release the victim's lease → returns to pending
                victim_job.status = "pending"
                victim_job.node_id = None
                victim_job.lease_token = None
                victim_job.leased_until = None
                victim_job.started_at = None
                victim_job.updated_at = now
                await db.flush()
                await _append_log(
                    db,
                    victim_job.job_id,
                    f"preempted by {urgent_job.job_id}: {reason}",
                    tenant_id=victim_job.tenant_id,
                )
                _audit.record_preemption(victim_job.job_id, urgent_job.job_id, reason)
                _governance.record_preemption(now)

                # Re-select with the freed slot
                node_snapshot = build_node_snapshot(
                    requesting_node,
                    active_lease_count=max(node_snapshot.active_lease_count - 1, 0),
                    reliability_score=reliability_map.get(payload.node_id, _get_dispatch_config().default_reliability_score),
                )
                selected = select_jobs_for_node(
                    [urgent_job],
                    node_snapshot,
                    active_node_snapshots,
                    now=now,
                    accepted_kinds=accepted_kinds,
                    recent_failed_job_ids=recent_failed_job_ids,
                    active_jobs_on_node=[j for j in active_jobs_on_node if j.job_id != victim_job.job_id],
                    limit=1,
                    placement_plan=placement_plan,
                )
                break  # one preemption per pull cycle

    # ── Scheduling backoff feedback ──────────────────────────────────
    _selected_ids = {s.job.job_id for s in selected}
    for c in candidates:
        if c.job_id not in _selected_ids:
            _governance.record_backoff_failure(c.job_id, now)
    # ── End Phase 4 ──────────────────────────────────────────────────

    # ── Phase 5: DLQ expired-deadline candidates ─────────────────────
    # Since the main query now filters out expired deadlines at SQL level,
    # run a separate lightweight scan to DLQ any pending jobs past deadline.
    dlq_query = (
        select(Job)
        .where(
            Job.tenant_id == payload.tenant_id,
            Job.status == "pending",
            Job.deadline_at.is_not(None),
            Job.deadline_at <= now,
        )
        .with_for_update(skip_locked=True)
        .limit(_get_dispatch_config().dlq_scan_limit)
    )
    dlq_result = await db.execute(dlq_query)
    for c in dlq_result.scalars().all():
        assert c.deadline_at is not None
        c.status = "failed"
        c.error_message = f"deadline expired at {c.deadline_at.isoformat()}"
        c.failure_category = "deadline_expired"
        c.updated_at = now
        await db.flush()
        await move_to_dead_letter_queue(redis, db, c)
        await _append_log(
            db,
            c.job_id,
            f"deadline expired: moved to DLQ ({c.deadline_at.isoformat()})",
            tenant_id=c.tenant_id,
        )
    # ── End Phase 5 ──────────────────────────────────────────────────

    leased_jobs: list[Job] = []
    _acquired_locks: list[str] = []
    for scored in selected:
        job = scored.job
        # ── Redis distributed lock prevents duplicate leases in multi-gateway ──
        _lock_name = f"job_dispatch:{payload.tenant_id}:{job.job_id}"
        if redis is not None:
            _lock_ok = await redis.acquire_lock(_lock_name, ttl=10)
            if not _lock_ok:
                continue  # Another gateway instance is leasing this job
            _acquired_locks.append(_lock_name)
        await _expire_previous_attempt_if_needed(db, job, now=now)
        job.status = "leased"
        job.node_id = payload.node_id
        job.attempt = int(job.attempt or 0) + 1
        job.attempt_count = int(getattr(job, "attempt_count", 0) or 0) + 1
        job.lease_token = _new_lease_token()
        job.result = None
        job.error_message = None
        job.completed_at = None
        job.started_at = now
        job.leased_until = now + datetime.timedelta(seconds=job.lease_seconds)
        job.updated_at = now
        await db.flush()
        await _create_attempt(db, job=job, node_id=payload.node_id, score=scored.score, now=now)
        await _append_log(
            db,
            job.job_id,
            f"job leased by {payload.node_id} attempt={job.attempt} score={scored.score} eligible_nodes={scored.eligible_nodes_count}",
            tenant_id=job.tenant_id,
        )
        existing_reservation = _reservation_mgr.get_reservation(job.job_id)
        if existing_reservation is not None and _reservation_mgr.cancel_reservation(job.job_id):
            await _publish_reservation_event(
                redis,
                "canceled",
                existing_reservation,
                reason="leased",
            )
        leased_jobs.append(job)
        _active_jobs_by_node.setdefault(payload.node_id, []).append(job)
        _governance.record_backoff_success(job.job_id)

        # Record placement in decision audit
        _audit.record_placement(
            job_id=job.job_id,
            score=scored.score,
            breakdown=scored.score_breakdown,
            eligible_nodes=scored.eligible_nodes_count,
        )

    _leased_job_ids = {job.job_id for job in leased_jobs}
    for candidate in sorted(
        candidates,
        key=lambda item: (-int(item.priority or 0), item.created_at, item.job_id),
    ):
        if candidate.job_id in _leased_job_ids:
            continue
        if _reservation_mgr.get_reservation(candidate.job_id) is not None:
            continue
        if int(candidate.priority or 0) < _reservation_mgr.config.reservation_min_priority:
            continue
        slot = choose_reservation_slot(
            candidate,
            active_node_snapshots,
            _active_jobs_by_node,
            now=now,
            accepted_kinds=None,
            reservation_mgr=_reservation_mgr,
        )
        if slot is None:
            continue
        reservation_node, reservation_start_at, _reservation_end_at = slot
        created_reservation = _reservation_mgr.create_reservation(
            candidate,
            reservation_node,
            start_at=reservation_start_at,
        )
        if created_reservation is None:
            continue
        await _append_log(
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
            reason="dispatch_backfill_plan",
        )

    # ── Scheduling metrics ────────────────────────────────────────────
    _dispatch_ms = (time.monotonic() - _dispatch_start) * 1000
    for _ in leased_jobs:
        _governance.record_placement_metric(_dispatch_ms)
    if candidates and not leased_jobs:
        _governance.record_rejection_metric("no_eligible_slot")

    # ── Flush decision audit (governance facade) ────────────────────
    await _governance.post_dispatch_audit(db, _audit, enabled=_ff_audit)

    responses = [_to_lease_response(job, now=now) for job in leased_jobs]
    if responses:
        await publish_control_event(
            redis,
            CHANNEL_JOB_EVENTS,
            "leased",
            {
                "node_id": payload.node_id,
                "jobs": [_to_response(job, now=now).model_dump(mode="json") for job in leased_jobs],
            },
        )
    # ── Release dispatch locks (lease committed, safe to unlock) ─────
    if redis is not None:
        for _lk in _acquired_locks:
            await redis.release_lock(_lk)
    return responses


@router.get("/{id}/explain", response_model=JobExplainResponse)
async def explain_job(
    id: str,
    current_user: dict[str, object] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> JobExplainResponse:
    from .database import _get_job_by_id

    tenant_id = str(current_user.get("tenant_id") or "default")
    now = _utcnow()
    job = await _get_job_by_id(db, tenant_id, id)

    nodes, active_lease_counts, reliability_map = await _load_node_metrics(db, tenant_id=tenant_id, now=now)
    snapshots = _build_snapshots(
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
            JobAttempt.created_at >= now - datetime.timedelta(hours=_get_dispatch_config().attempt_lookback_hours),
        )
        .distinct()
    )
    recent_failed_node_ids = {str(node_id) for node_id in failed_nodes_result.scalars().all() if node_id}
    eligible_nodes = count_eligible_nodes_for_job(job, snapshots, now=now)
    total_active_nodes = sum(1 for snapshot in snapshots if snapshot.enrollment_status == "active")

    # Same lease snapshot as pull_jobs: anti-affinity penalty uses active jobs per node
    leased_rows = await db.execute(
        select(Job).where(
            Job.tenant_id == tenant_id,
            Job.status == "leased",
        )
    )
    active_by_node: dict[str, list[Job]] = defaultdict(list)
    for leased in leased_rows.scalars().all():
        nid = getattr(leased, "node_id", None)
        if nid:
            active_by_node[str(nid)].append(leased)

    decisions: list[JobExplainDecisionResponse] = []
    for snapshot in snapshots:
        reasons = node_blockers_for_job(job, snapshot, now=now)
        eligible = not reasons
        score: int | None = None
        if eligible:
            score, _breakdown = score_job_for_node(  # type: ignore[misc, unused-ignore]
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

    # ── Governance context for explain trace ─────────────────────────
    from backend.core.queue_stratification import (
        get_aging_config,
        get_fair_scheduler,
        get_starvation_threshold_seconds,
    )

    _fcp_explain = get_failure_control_plane()
    _kind = getattr(job, "kind", "") or ""
    _kind_circuit = await _fcp_explain.get_kind_circuit_state(_kind, now=now) if _kind else None

    from backend.core.scheduling_governance import get_all_scheduling_flags

    _ff_flags = await get_all_scheduling_flags(db)

    _fair = get_fair_scheduler()
    _tenant_quota = _fair.get_quota(tenant_id)

    _placement_policy_name = "default"
    try:
        from backend.core.placement_policy import get_placement_policy

        _pp = get_placement_policy()
        _placement_policy_name = getattr(_pp, "name", "composite") or "composite"
    except Exception:
        pass

    governance = JobExplainGovernanceContext(
        feature_flags=_ff_flags,
        kind_circuit_state=_kind_circuit,
        node_quarantine_count=len(_fcp_explain._quarantine_until),
        connector_cooling_count=len(getattr(_fcp_explain, "_connector_cooling_until", {})),
        burst_active=_fcp_explain.is_in_burst(now=now),
        tenant_service_class=_tenant_quota.service_class,
        tenant_max_jobs_per_round=_tenant_quota.max_jobs_per_round,
        tenant_fair_share_weight=_tenant_quota.weight,
        placement_policy=_placement_policy_name,
        starvation_threshold_seconds=get_starvation_threshold_seconds(),
        aging_config=get_aging_config(),
    )

    return JobExplainResponse(
        job=_to_response(job, now=now),
        total_nodes=len(snapshots),
        eligible_nodes=eligible_nodes,
        selected_node_id=job.node_id,
        decisions=decisions,
        governance=governance,
    )
