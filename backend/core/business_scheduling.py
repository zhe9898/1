"""
Business Scheduling Optimizations

Provides advanced business scheduling features:
- Job priority inheritance from parent jobs
- Job dependency chains and DAG execution
- Job batching and gang scheduling
- Job preemption for high-priority jobs
- Job deadline scheduling
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.models.job import Job


def calculate_effective_priority(
    job: Job,
    *,
    now: datetime.datetime,
    parent_jobs: dict[str, Job] | None = None,
) -> int:
    """Calculate effective priority considering inheritance and deadlines.

    Priority adjustments:
    - Base priority: job.priority (0-100)
    - Parent inheritance: +10 if parent has higher priority
    - Deadline urgency: +0 to +30 based on time remaining
    - SLA breach risk: +20 if approaching SLA

    Returns: 0-160 (clamped effective priority)
    """
    base_priority = int(job.priority or 50)
    effective = base_priority

    # Parent priority inheritance
    parent_job_id = getattr(job, "parent_job_id", None)
    if parent_job_id and parent_jobs:
        parent = parent_jobs.get(parent_job_id)
        if parent and parent.priority > base_priority:
            effective += 10

    # Deadline urgency
    deadline = getattr(job, "deadline_at", None)
    if deadline and isinstance(deadline, datetime.datetime):
        time_remaining = (deadline - now).total_seconds()
        if time_remaining > 0:
            # Urgency increases as deadline approaches
            # 1 hour remaining = +30, 6 hours = +15, 24 hours = +5
            if time_remaining < 3600:  # < 1 hour
                effective += 30
            elif time_remaining < 21600:  # < 6 hours
                effective += 15
            elif time_remaining < 86400:  # < 24 hours
                effective += 5

    # SLA breach risk
    sla_seconds = getattr(job, "sla_seconds", None)
    if sla_seconds:
        age_seconds = (now - job.created_at).total_seconds()
        if age_seconds > sla_seconds * 0.8:  # 80% of SLA consumed
            effective += 20

    return max(0, min(160, effective))


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


def calculate_gang_scheduling_readiness(
    job: Job,
    gang_jobs: list[Job],
    available_slots: int,
) -> tuple[bool, str]:
    """Check if gang scheduling requirements are met.

    Gang scheduling: All jobs in a gang must be scheduled together.

    Returns: (ready, reason)
    """
    gang_id = getattr(job, "gang_id", None)
    if not gang_id:
        return True, ""

    # Find all jobs in the same gang
    gang_members = [j for j in gang_jobs if getattr(j, "gang_id", None) == gang_id]
    gang_size = len(gang_members)

    if gang_size == 0:
        return True, ""

    # Check if enough slots available for entire gang
    if available_slots < gang_size:
        return False, f"gang-scheduling:need-{gang_size}-slots:have-{available_slots}"

    # Check if all gang members are ready (no blocking dependencies)
    for member in gang_members:
        if member.status not in ("pending", "leased"):
            return False, f"gang-scheduling:member-{member.job_id}:status-{member.status}"

    return True, ""


def should_preempt_for_job(
    high_priority_job: Job,
    low_priority_job: Job,
    *,
    now: datetime.datetime,
) -> tuple[bool, str]:
    """Determine if low-priority job should be preempted for high-priority job.

    Preemption rules:
    - Only preempt if priority difference >= 40
    - Only preempt jobs that have been running < 5 minutes
    - Only preempt if high-priority job has deadline or SLA
    - Never preempt jobs marked as non-preemptible

    Returns: (should_preempt, reason)
    """
    # Check if low-priority job is preemptible
    if getattr(low_priority_job, "preemptible", True) is False:
        return False, "target-non-preemptible"

    # Check priority difference
    high_pri = int(high_priority_job.priority or 50)
    low_pri = int(low_priority_job.priority or 50)
    if high_pri - low_pri < 40:
        return False, f"priority-diff-too-small:{high_pri - low_pri}"

    # Check if low-priority job is still young (< 5 minutes)
    if low_priority_job.started_at:
        runtime = (now - low_priority_job.started_at).total_seconds()
        if runtime > 300:  # 5 minutes
            return False, f"target-runtime-too-long:{int(runtime)}s"

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

    # Larger batches get higher scores (up to 100 for 10+ jobs)
    return min(100, batch_size * 10)


def estimate_job_completion_time(
    job: Job,
    *,
    now: datetime.datetime,
) -> datetime.datetime | None:
    """Estimate when job will complete based on estimated duration.

    Returns: Estimated completion time, or None if cannot estimate
    """
    if job.status == "completed":
        return job.completed_at

    if job.status == "failed" or job.status == "canceled":
        return None

    estimated_duration = getattr(job, "estimated_duration_s", None)
    if not estimated_duration:
        return None

    if job.status == "leased" and job.started_at:
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
    estimated_duration = getattr(job, "estimated_duration_s", None) or 300

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

    if risk >= 0.9:
        return risk, "critical"
    elif risk >= 0.7:
        return risk, "high"
    elif risk >= 0.5:
        return risk, "medium"
    elif risk >= 0.3:
        return risk, "low"
    else:
        return risk, "none"


# ── Unified dispatch filter entry point ──────────────────────────────
# routes.py should call this single function instead of inlining filters.


def apply_business_filters(
    candidates: list[Job],
    *,
    completed_job_ids: set[str],
    available_slots: int,
    parent_jobs: dict[str, Job],
    now: datetime.datetime,
) -> list[Job]:
    """Apply dependency gate, gang gate, and effective-priority boost in one pass.

    Returns the filtered + boosted candidate list.  Does NOT touch DB —
    callers must pre-fetch completed_job_ids and parent_jobs.
    """
    # 1. Dependency gate
    after_deps: list[Job] = []
    for c in candidates:
        if c.depends_on:
            satisfied, _ = check_job_dependencies_satisfied(c, completed_job_ids)
            if not satisfied:
                continue
        after_deps.append(c)

    # 2. Gang gate
    after_gang: list[Job] = []
    for c in after_deps:
        if c.gang_id:
            ready, _ = calculate_gang_scheduling_readiness(c, after_deps, available_slots)
            if not ready:
                continue
        after_gang.append(c)

    # 3. Effective priority boost (deadline / SLA / parent inheritance)
    for c in after_gang:
        if c.deadline_at or c.sla_seconds or c.parent_job_id:
            boosted = calculate_effective_priority(c, now=now, parent_jobs=parent_jobs)
            if boosted > c.priority:
                c.priority = boosted

    return after_gang
