from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from backend.core.job_scheduler import SchedulerNodeSnapshot, node_blockers_for_job

if TYPE_CHECKING:
    from backend.core.backfill_scheduling import ReservationManager
    from backend.models.job import Job


def projected_job_completion_at(
    job: Job,
    *,
    now: datetime.datetime,
    default_duration_s: int,
) -> datetime.datetime:
    """Estimate when an active job will release its slot."""
    duration_s = max(int(getattr(job, "estimated_duration_s", None) or default_duration_s), 1)
    started_at = getattr(job, "started_at", None)
    leased_until = getattr(job, "leased_until", None)

    candidates = [now]
    if started_at is not None:
        candidates.append(started_at + datetime.timedelta(seconds=duration_s))
    if leased_until is not None:
        candidates.append(leased_until)
    if started_at is None and leased_until is None:
        candidates.append(now + datetime.timedelta(seconds=duration_s))
    return max(candidates)


def estimate_node_next_slot_at(
    node: SchedulerNodeSnapshot,
    active_jobs_on_node: list[Job],
    *,
    now: datetime.datetime,
    default_duration_s: int,
) -> datetime.datetime:
    """Estimate the first instant when the node can accept one more job."""
    if node.active_lease_count < max(node.max_concurrency, 1):
        return now

    projected_ends = sorted(projected_job_completion_at(job, now=now, default_duration_s=default_duration_s) for job in active_jobs_on_node)
    if not projected_ends:
        return now
    return max(projected_ends[0], now)


def reservation_structural_blockers(
    job: Job,
    node: SchedulerNodeSnapshot,
    *,
    now: datetime.datetime,
    accepted_kinds: set[str] | None = None,
) -> list[str]:
    """Blockers relevant for future reservations, excluding current saturation."""
    return [blocker for blocker in node_blockers_for_job(job, node, now=now, accepted_kinds=accepted_kinds) if blocker != "capacity=full"]


def choose_reservation_slot(
    job: Job,
    nodes: list[SchedulerNodeSnapshot],
    active_jobs_by_node: dict[str, list[Job]],
    *,
    now: datetime.datetime,
    accepted_kinds: set[str] | None,
    reservation_mgr: ReservationManager,
) -> tuple[SchedulerNodeSnapshot, datetime.datetime, datetime.datetime] | None:
    """Choose the earliest feasible reservation window across eligible nodes."""
    duration_s = max(
        int(getattr(job, "estimated_duration_s", None) or reservation_mgr.config.default_estimated_duration_s),
        1,
    )
    tenant_id = str(getattr(job, "tenant_id", "default") or "default")
    best: tuple[datetime.datetime, datetime.datetime, str, SchedulerNodeSnapshot] | None = None

    for node in nodes:
        blockers = reservation_structural_blockers(job, node, now=now, accepted_kinds=accepted_kinds)
        if blockers:
            continue
        earliest_start = estimate_node_next_slot_at(
            node,
            active_jobs_by_node.get(node.node_id, []),
            now=now,
            default_duration_s=reservation_mgr.config.default_estimated_duration_s,
        )
        window = reservation_mgr.find_backfill_window(
            node,
            tenant_id=tenant_id,
            now=earliest_start,
            required_duration_s=duration_s,
        )
        if window is None:
            continue
        start_at, end_at = window
        candidate = (start_at, end_at, node.node_id, node)
        if best is None or candidate[:3] < best[:3]:
            best = candidate

    if best is None:
        return None
    start_at, end_at, _node_id, node = best
    return node, start_at, end_at
