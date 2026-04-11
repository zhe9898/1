"""
Business Scheduling Engine

Utility functions for job scheduling: priority boosting, dependency
checking, preemption, batch scoring, SLA risk.
Constraint pipeline classes live in ``scheduling_constraints.py``.
Gang scheduling coordination lives in ``gang_scheduler.py``.

**Module boundary**
This module owns *scheduling decision logic* that operates on job metadata:
priority adjustments, preemption decisions, SLA breach risk, and dependency
satisfaction.  It does **not** own:

- Constraint classes / the pipeline engine 鈫?``scheduling_constraints.py``
- Gang readiness / all-or-nothing coordination 鈫?``gang_scheduler.py``
- Job kind metadata, resource profiles, QoS classes 鈫?``workload_semantics.py``
- Queue ordering / aging formulas / tenant fair-share 鈫?``queue_stratification.py``
"""

from __future__ import annotations

import datetime
import math
from typing import TYPE_CHECKING

from backend.kernel.policy.types import (
    BatchScoringConfig,
    PreemptionPolicy,
    PriorityBoostConfig,
    SLARiskConfig,
)
from backend.runtime.execution.job_status import normalize_job_status
from backend.runtime.scheduling.scheduling_constraints import (  # noqa: F401 鈥?re-export
    ConnectorCoolingGate,
    DeadlineExpiryGate,
    DependencyGate,
    GangSchedulingGate,
    PriorityBoostModifier,
    SchedulingConstraint,
    SchedulingContext,
    SchedulingEngine,
    TenantFairShareGate,
    get_scheduling_engine,
)

if TYPE_CHECKING:
    from backend.models.job import Job


def _get_priority_boost_config() -> PriorityBoostConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.priority_boost


def _get_preemption_policy() -> PreemptionPolicy:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.preemption


def _get_batch_scoring_config() -> BatchScoringConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.batch_scoring


def _get_sla_risk_config() -> SLARiskConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.sla_risk


def calculate_boosted_priority(
    job: Job,
    *,
    now: datetime.datetime,
    parent_jobs: dict[str, Job] | None = None,
) -> int:
    """Calculate boosted priority considering inheritance and deadlines.

    Priority adjustments:
    - Base priority: job.priority (0-100)
    - Parent inheritance: +10 if parent has higher priority
    - Deadline urgency: +0 to +30 based on time remaining
    - SLA breach risk: +20 if approaching SLA

    Returns: 0-160 (clamped effective priority)
    """
    _pbc = _get_priority_boost_config()
    base_priority = int(job.priority or _pbc.default_priority)
    effective = base_priority

    # Parent priority inheritance
    parent_job_id = getattr(job, "parent_job_id", None)
    if parent_job_id and parent_jobs:
        parent = parent_jobs.get(parent_job_id)
        if parent and parent.priority > base_priority:
            effective += _pbc.parent_inheritance_bonus

    # Deadline urgency 鈥?continuous exponential curve.
    # Smoothly ramps from +0 (far away) to +deadline_urgency_max (imminent).
    deadline = getattr(job, "deadline_at", None)
    if deadline and isinstance(deadline, datetime.datetime):
        time_remaining = (deadline - now).total_seconds()
        if time_remaining > 0:
            effective += min(
                _pbc.deadline_urgency_max,
                int(
                    _pbc.deadline_urgency_max
                    * math.exp(
                        -time_remaining / _pbc.deadline_half_life_seconds,
                    )
                ),
            )

    # SLA breach risk
    sla_seconds = getattr(job, "sla_seconds", None)
    if sla_seconds:
        age_seconds = (now - job.created_at).total_seconds()
        if age_seconds > sla_seconds * _pbc.sla_threshold_ratio:
            effective += _pbc.sla_breach_bonus

    from backend.kernel.policy.policy_store import get_policy_store

    _sw = get_policy_store().active.scoring
    return max(0, min(_sw.priority_max, effective))


def check_job_dependencies_satisfied(
    job: Job,
    completed_job_ids: set[str],
) -> tuple[bool, list[str]]:
    """Check if job's dependencies are satisfied.

    Returns: (satisfied, blocking_job_ids)
    """
    depends_on = getattr(job, "depends_on", None) or []
    if not depends_on:
        return True, []

    blocking = [dep_id for dep_id in depends_on if dep_id not in completed_job_ids]
    return len(blocking) == 0, blocking


def should_preempt_for_job(
    high_priority_job: Job,
    low_priority_job: Job,
    *,
    now: datetime.datetime,
) -> tuple[bool, str]:
    """Determine if low-priority job should be preempted for high-priority job.

    Preemption rules:
    - Only preempt if priority difference >= min_priority_diff
    - Only preempt jobs that have been running < max_victim_runtime_seconds
    - Only preempt if high-priority job has deadline or SLA
    - Never preempt jobs marked as non-preemptible

    Returns: (should_preempt, reason)
    """
    # Check if low-priority job is preemptible
    if getattr(low_priority_job, "preemptible", True) is False:
        return False, "target-non-preemptible"

    _pp = _get_preemption_policy()

    # Check priority difference
    high_pri = int(high_priority_job.priority or 50)
    low_pri = int(low_priority_job.priority or 50)
    if high_pri - low_pri < _pp.min_priority_diff:
        return False, f"priority-diff-too-small:{high_pri - low_pri}"

    # Check if low-priority job is still young
    # Progress-aware: if estimated_duration_s is set, consider completion %.
    # A job 90% done is more expensive to preempt than one just started.
    if low_priority_job.started_at:
        runtime = (now - low_priority_job.started_at).total_seconds()
        if runtime > _pp.max_victim_runtime_seconds:
            return False, f"target-runtime-too-long:{int(runtime)}s"
        estimated = getattr(low_priority_job, "estimated_duration_s", None)
        if estimated and estimated > 0:
            progress = min(runtime / estimated, 1.0)
            if progress > _pp.max_victim_progress:
                return False, f"target-progress-too-high:{int(progress * 100)}%"

    # Check if high-priority job has urgency
    has_deadline = getattr(high_priority_job, "deadline_at", None) is not None
    has_sla = getattr(high_priority_job, "sla_seconds", None) is not None
    if not (has_deadline or has_sla):
        return False, "no-urgency"

    return True, f"preempt:priority-diff={high_pri - low_pri}"


def calculate_batch_scheduling_score(
    job: Job,
    batch_jobs: list[Job],
) -> int:
    """Calculate score for batch scheduling optimization.

    Batch scheduling: Group similar jobs together for efficiency.

    Returns: 0-100 (higher = better for batching)
    """
    batch_key = getattr(job, "batch_key", None)
    if not batch_key:
        return 0

    # Count jobs with same batch_key
    same_batch = [j for j in batch_jobs if getattr(j, "batch_key", None) == batch_key]
    batch_size = len(same_batch)

    if batch_size <= 1:
        return 0

    # Larger batches get higher scores
    _bsc = _get_batch_scoring_config()
    return min(_bsc.max_score, batch_size * _bsc.score_per_member)


def estimate_job_completion_time(
    job: Job,
    *,
    now: datetime.datetime,
) -> datetime.datetime | None:
    """Estimate when job will complete based on estimated duration.

    Returns: Estimated completion time, or None if cannot estimate
    """
    normalized_status = normalize_job_status(job.status) or "pending"

    if normalized_status == "completed":
        return job.completed_at

    if normalized_status in {"failed", "cancelled"}:
        return None

    estimated_duration = getattr(job, "estimated_duration_s", None)
    if not estimated_duration:
        return None

    if normalized_status == "leased" and job.started_at:
        # Job is running, estimate based on remaining time
        elapsed = (now - job.started_at).total_seconds()
        remaining = max(0, estimated_duration - elapsed)
        return now + datetime.timedelta(seconds=remaining)
    else:
        # Job is pending, estimate based on full duration
        return now + datetime.timedelta(seconds=estimated_duration)


def calculate_sla_breach_risk(
    job: Job,
    *,
    now: datetime.datetime,
) -> tuple[float, str]:
    """Calculate risk of SLA breach (0.0 = no risk, 1.0 = breached).

    Returns: (risk_score, risk_level)
    - risk_level: "none", "low", "medium", "high", "critical", "breached"
    """
    sla_seconds = getattr(job, "sla_seconds", None)
    if not sla_seconds:
        return 0.0, "none"

    age_seconds = (now - job.created_at).total_seconds()
    estimated_duration = getattr(job, "estimated_duration_s", None) or _get_sla_risk_config().default_estimated_duration_s

    if job.status == "completed":
        # Already completed, check if SLA was met
        if age_seconds > sla_seconds:
            return 1.0, "breached"
        return 0.0, "none"

    # Estimate completion time
    if job.status == "leased" and job.started_at:
        elapsed = (now - job.started_at).total_seconds()
        remaining = max(0, estimated_duration - elapsed)
        estimated_completion = age_seconds + remaining
    else:
        # Job is pending, add estimated duration
        estimated_completion = age_seconds + estimated_duration

    # Calculate risk
    if estimated_completion > sla_seconds:
        return 1.0, "breached"

    risk = estimated_completion / sla_seconds

    _src = _get_sla_risk_config()
    if risk >= _src.critical_threshold:
        return risk, "critical"
    elif risk >= _src.high_threshold:
        return risk, "high"
    elif risk >= _src.medium_threshold:
        return risk, "medium"
    elif risk >= _src.low_threshold:
        return risk, "low"
    else:
        return risk, "none"


# 鈹€鈹€ Unified dispatch filter entry point 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# routes.py should call this single function instead of inlining filters.


def apply_business_filters(
    candidates: list[Job],
    *,
    completed_job_ids: set[str],
    available_slots: int,
    parent_jobs: dict[str, Job],
    now: datetime.datetime,
    extra_context: dict[str, object] | None = None,
) -> list[Job]:
    """Apply hard scheduling gates and priority boost via the constraint engine.

    This is the single entry point called by dispatch.py. It delegates to
    the class-based ``SchedulingEngine`` which evaluates all registered
    constraints in order.

    Returns the filtered + boosted candidate list.  Does NOT touch DB 鈥?    callers must pre-fetch completed_job_ids and parent_jobs.

    ``extra_context`` is merged into ``ctx.data`` before evaluation,
    allowing callers to inject quota accounts, fair-share ratios, etc.
    """
    ctx = SchedulingContext(
        now=now,
        completed_job_ids=completed_job_ids,
        available_slots=available_slots,
        parent_jobs=parent_jobs,
    )
    if extra_context:
        ctx.data.update(extra_context)
    engine = get_scheduling_engine()
    return engine.run(candidates, ctx)


def find_preemption_candidates(
    urgent_jobs: list[Job],
    running_jobs: list[Job],
    *,
    now: datetime.datetime,
) -> list[tuple[Job, Job, str]]:
    """Identify (urgent_job, evictable_job, reason) triples.

    This function evaluates all urgent (high-priority) pending jobs against
    currently-running low-priority jobs on the same node and returns pairs
    where preemption is justified.

    Returns a list of (to_schedule, to_preempt, reason) tuples.
    Each running job appears at most once (lowest-priority match wins).
    """
    if not urgent_jobs or not running_jobs:
        return []

    # Sort running jobs by priority ascending so cheapest eviction comes first
    evictable = sorted(running_jobs, key=lambda j: int(j.priority or 0))
    claimed: set[str] = set()
    results: list[tuple[Job, Job, str]] = []

    for urgent in sorted(urgent_jobs, key=lambda j: -int(j.priority or 0)):
        for victim in evictable:
            if victim.job_id in claimed:
                continue
            should, reason = should_preempt_for_job(urgent, victim, now=now)
            if should:
                results.append((urgent, victim, reason))
                claimed.add(victim.job_id)
                break  # one eviction per urgent job

    return results
