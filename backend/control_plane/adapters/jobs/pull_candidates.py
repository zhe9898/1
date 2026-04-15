from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Integer, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import Job

from .models import JobPullRequest
from .pull_contracts import PullCandidateContext, PullJobsDependencies

if TYPE_CHECKING:
    from backend.kernel.policy.types import DispatchConfig


def _get_dispatch_config() -> DispatchConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.dispatch


def _build_dispatch_candidate_where(*, tenant_id: str, now: Any) -> list[Any]:
    return [
        Job.tenant_id == tenant_id,
        or_(
            (Job.status == "pending") & (or_(Job.retry_at.is_(None), Job.retry_at <= now)),
            (Job.status == "leased") & (Job.leased_until.is_not(None)) & (Job.leased_until < now),
        ),
        or_(Job.deadline_at.is_(None), Job.deadline_at > now),
    ]


def _build_effective_priority_expression(*, now: Any) -> Any:
    from backend.kernel.policy.policy_store import get_policy_store

    queue_config = get_policy_store().active.queue
    age_seconds = func.greatest(func.extract("epoch", literal(now) - Job.created_at), literal(0))
    layers = queue_config.priority_layers
    layer_muls = queue_config.layer_aging_multipliers
    sorted_layers = sorted(layers.items(), key=lambda kv: kv[1][0], reverse=True)
    case_whens = [(Job.priority >= lo, literal(float(layer_muls.get(name, 1.0)))) for name, (lo, _hi) in sorted_layers[:-1]]
    else_mul = float(layer_muls.get(sorted_layers[-1][0], 1.0)) if sorted_layers else 1.0
    layer_multiplier = case(*case_whens, else_=literal(else_mul))

    aging_interval = float(queue_config.aging.interval_seconds)
    bonus_per_interval = float(queue_config.aging.bonus_per_interval)
    aging_cap = float(queue_config.aging.max_bonus)
    aging_bonus = func.least(
        func.sqrt(age_seconds / literal(aging_interval)) * layer_multiplier * literal(bonus_per_interval),
        literal(aging_cap),
    )
    return func.least(Job.priority + func.cast(aging_bonus, Integer), literal(100))


def _get_starvation_rescue_limit(*, payload_limit: int, dispatch_config: DispatchConfig) -> int:
    if dispatch_config.starvation_rescue_max <= 0:
        return 0
    scaled_limit = max(
        payload_limit * max(dispatch_config.starvation_rescue_multiplier, 0),
        dispatch_config.starvation_rescue_min,
    )
    return min(scaled_limit, dispatch_config.starvation_rescue_max)


def _merge_dispatch_candidates(primary: list[Job], rescue: list[Job]) -> list[Job]:
    merged: list[Job] = []
    seen_job_ids: set[str] = set()
    for candidate in [*primary, *rescue]:
        if candidate.job_id in seen_job_ids:
            continue
        seen_job_ids.add(candidate.job_id)
        merged.append(candidate)
    return merged


async def _query_dispatch_candidates(
    db: AsyncSession,
    *,
    tenant_id: str,
    now: Any,
    accepted_kinds: set[str],
    candidate_limit: int,
) -> list[Job]:
    base_where = _build_dispatch_candidate_where(tenant_id=tenant_id, now=now)
    effective_priority = _build_effective_priority_expression(now=now)
    query = (
        select(Job)
        .where(*base_where)
        .with_for_update(skip_locked=True)
        .order_by(effective_priority.desc(), Job.created_at.asc(), Job.job_id.asc())
        .limit(candidate_limit)
    )
    if accepted_kinds:
        query = query.where(Job.kind.in_(accepted_kinds))

    result = await db.execute(query)
    return list(result.scalars().all())


async def _query_starved_dispatch_candidates(
    db: AsyncSession,
    *,
    tenant_id: str,
    now: Any,
    accepted_kinds: set[str],
    rescue_limit: int,
) -> list[Job]:
    if rescue_limit <= 0:
        return []

    from backend.kernel.policy.policy_store import get_policy_store

    queue_config = get_policy_store().active.queue
    starvation_threshold_seconds = max(int(queue_config.starvation_threshold_seconds), 0)
    if starvation_threshold_seconds <= 0:
        return []

    base_where = _build_dispatch_candidate_where(tenant_id=tenant_id, now=now)
    starvation_cutoff = now - timedelta(seconds=starvation_threshold_seconds)
    query = (
        select(Job)
        .where(*base_where, Job.created_at <= starvation_cutoff)
        .with_for_update(skip_locked=True)
        .order_by(Job.created_at.asc(), Job.priority.desc(), Job.job_id.asc())
        .limit(rescue_limit)
    )
    if accepted_kinds:
        query = query.where(Job.kind.in_(accepted_kinds))

    result = await db.execute(query)
    return list(result.scalars().all())


async def _filter_dispatch_candidates(
    candidates: list[Job],
    *,
    governance: Any,
    failure_control_plane: Any,
    requesting_executor: str | None,
    ff_executor_val: bool,
    audit: Any,
    now: Any,
) -> list[Job]:
    pre_backoff = len(candidates)
    candidates = [candidate for candidate in candidates if not governance.should_skip_backoff(candidate.job_id, now)]
    backoff_skipped = pre_backoff - len(candidates)
    if backoff_skipped:
        governance.record_backoff_skip_metric()

    from backend.runtime.scheduling.queue_stratification import sort_jobs_by_stratified_priority

    sorted_candidates = cast(list[Job], sort_jobs_by_stratified_priority(candidates, now=now, aging_enabled=True))
    filtered_candidates: list[Job] = []
    for candidate in sorted_candidates:
        kind = getattr(candidate, "kind", None) or ""
        if kind:
            circuit_state = await failure_control_plane.get_kind_circuit_state(kind, now=now)
            if circuit_state == "open":
                audit.record_rejection(candidate.job_id, f"kind_circuit_open:{kind}")
                continue
        if ff_executor_val and kind:
            executor_filter = governance.filter_by_executor_contract(requesting_executor, kind)
            if not executor_filter.compatible:
                audit.record_rejection(candidate.job_id, f"executor_kind_incompat:{executor_filter.reason}")
                continue
        filtered_candidates.append(candidate)
    return filtered_candidates


async def _load_completed_dependency_ids(db: AsyncSession, *, tenant_id: str, candidates: list[Job]) -> set[str]:
    dependency_ids = {dependency_id for candidate in candidates for dependency_id in (candidate.depends_on or [])}
    if not dependency_ids:
        return set()
    dep_result = await db.execute(
        select(Job.job_id).where(
            Job.tenant_id == tenant_id,
            Job.job_id.in_(dependency_ids),
            Job.status == "completed",
        )
    )
    return set(dep_result.scalars().all())


async def _load_parent_jobs(db: AsyncSession, *, tenant_id: str, candidates: list[Job]) -> dict[str, Job]:
    parent_ids = {candidate.parent_job_id for candidate in candidates if candidate.parent_job_id}
    if not parent_ids:
        return {}
    parent_result = await db.execute(select(Job).where(Job.tenant_id == tenant_id, Job.job_id.in_(parent_ids)))
    return {job.job_id: job for job in parent_result.scalars().all()}


def _group_active_jobs_by_node(leased_jobs: list[Job]) -> dict[str, list[Job]]:
    active_jobs_by_node: dict[str, list[Job]] = defaultdict(list)
    for leased_job in leased_jobs:
        leased_node_id = getattr(leased_job, "node_id", None)
        if leased_node_id:
            active_jobs_by_node[str(leased_node_id)].append(leased_job)
    return active_jobs_by_node


def _build_quota_context(leased_jobs: list[Job]) -> dict[str, object]:
    from backend.runtime.scheduling.quota_aware_scheduling import FairShareCalculator, ResourceUsage, build_quota_accounts

    extra_ctx: dict[str, object] = {}
    quota_accounts = build_quota_accounts(leased_jobs)
    extra_ctx["_quota_accounts"] = quota_accounts

    cluster_totals = ResourceUsage()
    for account in quota_accounts.values():
        cluster_totals.cpu_cores += account.usage.cpu_cores
        cluster_totals.memory_mb += account.usage.memory_mb
        cluster_totals.gpu_vram_mb += account.usage.gpu_vram_mb
        cluster_totals.concurrent_jobs += account.usage.concurrent_jobs
    extra_ctx["_fair_share_ratios"] = FairShareCalculator.compute_fair_shares(quota_accounts, cluster_totals)
    return extra_ctx


async def _load_dispatch_candidate_window(
    db: AsyncSession,
    *,
    payload: JobPullRequest,
    now: Any,
    accepted_kinds: set[str],
    candidate_limit: int,
) -> tuple[list[Job], dict[str, int]]:
    candidates = await _query_dispatch_candidates(
        db,
        tenant_id=payload.tenant_id,
        now=now,
        accepted_kinds=accepted_kinds,
        candidate_limit=candidate_limit,
    )
    primary_count = len(candidates)
    starvation_rescue_limit = 0
    starvation_rescue_added = 0
    if primary_count >= candidate_limit:
        dispatch_config = _get_dispatch_config()
        starvation_rescue_limit = _get_starvation_rescue_limit(
            payload_limit=payload.limit,
            dispatch_config=dispatch_config,
        )
        if starvation_rescue_limit > 0:
            starvation_rescue_candidates = await _query_starved_dispatch_candidates(
                db,
                tenant_id=payload.tenant_id,
                now=now,
                accepted_kinds=accepted_kinds,
                rescue_limit=starvation_rescue_limit,
            )
            merged_candidates = _merge_dispatch_candidates(candidates, starvation_rescue_candidates)
            starvation_rescue_added = max(len(merged_candidates) - primary_count, 0)
            candidates = merged_candidates
    return candidates, {
        "primary_limit": candidate_limit,
        "primary_count": primary_count,
        "starvation_rescue_limit": starvation_rescue_limit,
        "starvation_rescue_added": starvation_rescue_added,
        "total_candidates_before_filters": len(candidates),
    }


async def _build_candidate_context(
    *,
    db: AsyncSession,
    payload: JobPullRequest,
    now: Any,
    node_snapshot: Any,
    governance: Any,
    failure_control_plane: Any,
    ff_executor_val: bool,
    accepted_kinds: set[str],
    candidate_limit: int,
    active_node_snapshots: list[Any],
    audit: Any,
    deps: PullJobsDependencies,
) -> PullCandidateContext:
    del active_node_snapshots

    candidates, candidate_window = await _load_dispatch_candidate_window(
        db,
        payload=payload,
        now=now,
        accepted_kinds=accepted_kinds,
        candidate_limit=candidate_limit,
    )
    audit.context["candidate_window"] = candidate_window
    candidates = await _filter_dispatch_candidates(
        candidates,
        governance=governance,
        failure_control_plane=failure_control_plane,
        requesting_executor=node_snapshot.executor,
        ff_executor_val=ff_executor_val,
        audit=audit,
        now=now,
    )

    completed_dep_ids = await _load_completed_dependency_ids(db, tenant_id=payload.tenant_id, candidates=candidates)
    parent_jobs = await _load_parent_jobs(db, tenant_id=payload.tenant_id, candidates=candidates)
    available_slots = max(node_snapshot.max_concurrency - node_snapshot.active_lease_count, 0)

    leased_result = await db.execute(select(Job).where(Job.tenant_id == payload.tenant_id, Job.status == "leased"))
    leased_jobs = list(leased_result.scalars().all())
    active_jobs_by_node = _group_active_jobs_by_node(leased_jobs)

    from backend.runtime.scheduling.business_scheduling import apply_business_filters

    candidates = apply_business_filters(
        candidates,
        completed_job_ids=completed_dep_ids,
        available_slots=available_slots,
        parent_jobs=parent_jobs,
        now=now,
        extra_context=_build_quota_context(leased_jobs),
    )

    recent_failed_job_ids = await deps.load_recent_failed_job_ids(
        db,
        tenant_id=payload.tenant_id,
        node_id=payload.node_id,
        job_ids=[job.job_id for job in candidates],
        now=now,
    )
    audit.candidates_count = len(candidates)

    return PullCandidateContext(
        candidates=candidates,
        active_jobs_by_node=active_jobs_by_node,
        recent_failed_job_ids=recent_failed_job_ids,
        available_slots=available_slots,
    )
