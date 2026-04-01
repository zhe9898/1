"""Gang Scheduling Coordinator — all-or-nothing dispatch for job groups.

The existing ``GangSchedulingGate`` is a per-node slot check. Real gang
scheduling requires cross-node coordination:

1. All gang members must be schedulable *simultaneously*.
2. If any member cannot be placed, none are dispatched.
3. Members may span multiple nodes (multi-node gang).

This module provides:

- ``GangCoordinator`` — stateful coordinator that collects gang member
  readiness across the dispatch cycle and enforces all-or-nothing.
- ``GangPermitPlugin`` — a ``PermitPlugin`` for the scheduling framework
  that holds gang members until all are ready.
- ``GangPlacementSolver`` — extends ``PlacementSolver`` to handle gang
  groups atomically during global placement.

References:
- Slurm: ``--ntasks`` + ``--nodes`` gang semantics
- Nomad: task groups (all tasks in a group co-scheduled)
- K8s: coscheduling/gang-scheduling plugin (PodGroup)
- Volcano: ``vcjob`` with minAvailable
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.job_scheduler import SchedulerNodeSnapshot
    from backend.core.scheduling_constraints import SchedulingContext
    from backend.models.job import Job

logger = logging.getLogger(__name__)


# =====================================================================
# Gang Group metadata
# =====================================================================


@dataclass(slots=True)
class GangGroup:
    """Represents a set of jobs that must be co-scheduled."""

    gang_id: str
    members: list[Job] = field(default_factory=list)
    min_available: int = 0  # 0 = all members required
    placed_count: int = 0
    placement: dict[str, str] = field(default_factory=dict)  # job_id → node_id

    @property
    def required_count(self) -> int:
        return self.min_available if self.min_available > 0 else len(self.members)

    @property
    def is_satisfiable(self) -> bool:
        return self.placed_count >= self.required_count

    @property
    def pending_count(self) -> int:
        return max(0, self.required_count - self.placed_count)


# =====================================================================
# Gang Coordinator — per-cycle stateful coordination
# =====================================================================


class GangCoordinator:
    """Coordinate gang scheduling across a single dispatch cycle.

    Usage::

        coord = GangCoordinator()

        # Phase 1: register all pending gang jobs
        for job in candidates:
            coord.register(job)

        # Phase 2: check readiness per job
        for job in candidates:
            if coord.is_gang_ready(job, available_slots):
                dispatch(job)

        # Phase 3: commit or rollback
        for group in coord.ready_groups():
            for member in group.members:
                lease(member)
    """

    def __init__(self) -> None:
        self._groups: dict[str, GangGroup] = {}

    def register(self, job: Job) -> None:
        """Register a job and its gang group."""
        gang_id = getattr(job, "gang_id", None)
        if not gang_id:
            return
        if gang_id not in self._groups:
            min_avail = getattr(job, "gang_min_available", 0) or 0
            self._groups[gang_id] = GangGroup(
                gang_id=gang_id,
                min_available=int(min_avail),
            )
        self._groups[gang_id].members.append(job)

    def get_group(self, gang_id: str) -> GangGroup | None:
        return self._groups.get(gang_id)

    def mark_placed(self, job: Job, node_id: str) -> None:
        """Record that a gang member has been tentatively placed."""
        gang_id = getattr(job, "gang_id", None)
        if not gang_id or gang_id not in self._groups:
            return
        group = self._groups[gang_id]
        if job.job_id not in group.placement:
            group.placement[job.job_id] = node_id
            group.placed_count += 1

    def is_gang_ready(self, job: Job, total_available_slots: int) -> bool:
        """Check if job's gang group can be fully scheduled.

        ``total_available_slots`` is the cluster-wide slots, not per-node.
        For non-gang jobs, always returns True.
        """
        gang_id = getattr(job, "gang_id", None)
        if not gang_id:
            return True
        group = self._groups.get(gang_id)
        if not group:
            return True

        # All members must be in pending/leased state
        for member in group.members:
            if member.status not in ("pending", "leased"):
                return False

        # Must have enough cluster-wide slots
        return total_available_slots >= group.required_count

    def ready_groups(self) -> list[GangGroup]:
        """Return gang groups that are fully satisfiable."""
        return [g for g in self._groups.values() if g.is_satisfiable]

    def unsatisfied_groups(self) -> list[GangGroup]:
        """Return gang groups that cannot be fully scheduled."""
        return [g for g in self._groups.values() if not g.is_satisfiable]

    def gang_member_job_ids(self, gang_id: str) -> set[str]:
        """Return all job IDs in a gang group."""
        group = self._groups.get(gang_id)
        if not group:
            return set()
        return {m.job_id for m in group.members}


# =====================================================================
# Gang-aware placement solver extension
# =====================================================================


def solve_gang_placement(
    groups: list[GangGroup],
    nodes: list[SchedulerNodeSnapshot],
    *,
    score_fn: object | None = None,
) -> dict[str, dict[str, str]]:
    """Solve placement for multiple gang groups atomically.

    For each gang group, finds a set of nodes that can host all required
    members. Uses greedy bin-packing: assign members to nodes with the
    most remaining capacity.

    Returns ``{gang_id: {job_id: node_id}}`` for satisfiable groups.
    Unsatisfiable groups are omitted.
    """
    # Build remaining capacity map
    remaining_cap: dict[str, int] = {}
    for n in nodes:
        cap = max(n.max_concurrency - n.active_lease_count, 0)
        if cap > 0:
            remaining_cap[n.node_id] = cap

    result: dict[str, dict[str, str]] = {}

    # Sort groups by required_count descending (hardest first)
    sorted_groups = sorted(groups, key=lambda g: -g.required_count)

    for group in sorted_groups:
        needed = group.required_count
        if needed <= 0:
            continue

        # Sort nodes by remaining capacity descending
        available_nodes = sorted(
            [(nid, cap) for nid, cap in remaining_cap.items() if cap > 0],
            key=lambda x: -x[1],
        )

        # Check total available
        total_avail = sum(cap for _, cap in available_nodes)
        if total_avail < needed:
            continue  # Cannot satisfy — skip

        # Greedy assign: spread across nodes
        placement: dict[str, str] = {}
        members_to_place = group.members[:needed]
        node_idx = 0

        for member in members_to_place:
            while node_idx < len(available_nodes):
                nid, cap = available_nodes[node_idx]
                if cap > 0:
                    placement[member.job_id] = nid
                    available_nodes[node_idx] = (nid, cap - 1)
                    remaining_cap[nid] = cap - 1
                    break
                node_idx += 1
            else:
                break  # No more nodes

        if len(placement) >= needed:
            result[group.gang_id] = placement
            group.placement = placement
            group.placed_count = len(placement)

    return result


# =====================================================================
# Framework integration: GangPermitPlugin
# =====================================================================


class GangPermitPlugin:
    """Permit-phase plugin that enforces all-or-nothing gang dispatch.

    When used with ``SchedulingPipeline``, jobs with a gang_id are held
    (WAIT) until the coordinator confirms all members are placed.
    Non-gang jobs pass immediately.
    """

    name = "gang_permit"

    def __init__(self, coordinator: GangCoordinator) -> None:
        self._coord = coordinator

    def permit(self, job: Job, ctx: SchedulingContext) -> object:
        from backend.core.scheduling_framework import PluginResult, PluginStatus

        gang_id = getattr(job, "gang_id", None)
        if not gang_id:
            return PluginResult(status=PluginStatus.SUCCESS)

        group = self._coord.get_group(gang_id)
        if not group:
            return PluginResult(status=PluginStatus.SUCCESS)

        if group.is_satisfiable:
            return PluginResult(status=PluginStatus.SUCCESS)

        return PluginResult(
            status=PluginStatus.WAIT,
            reason=f"gang:{gang_id}:waiting:{group.pending_count}/{group.required_count}",
        )


# =====================================================================
# Module-level singleton
# =====================================================================

_coordinator: GangCoordinator | None = None


def get_gang_coordinator() -> GangCoordinator:
    """Return the module-level gang coordinator (reset per cycle)."""
    global _coordinator
    if _coordinator is None:
        _coordinator = GangCoordinator()
    return _coordinator


def reset_gang_coordinator() -> GangCoordinator:
    """Create a fresh coordinator for a new dispatch cycle."""
    global _coordinator
    _coordinator = GangCoordinator()
    return _coordinator
