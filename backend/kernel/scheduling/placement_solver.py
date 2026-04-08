"""Global Placement Solver 鈥?cross-node constraint-satisfaction optimisation.

Extracted from ``job_scheduler.py`` to reduce that module's size.

Dependency contract (no cycles):
    scheduling_candidates  鈫? placement_solver  鈫? (lazy runtime) job_scheduler

Runtime imports from ``job_scheduler`` (``is_node_eligible``,
``job_matches_node``) are deferred inside method bodies so this module loads
cleanly even when ``job_scheduler`` has not finished its own import sequence.

All public symbols remain importable from ``backend.kernel.scheduling.job_scheduler`` via
re-exports for backward compatibility.
"""

from __future__ import annotations

import heapq
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.kernel.scheduling.job_scoring import score_job_for_node
from backend.kernel.scheduling.scheduling_candidates import (
    _candidate_nodes_for_job,
    _has_items_attr,
    _int_attr,
    _job_attr,
    _job_routing_key,
    _text_attr,
    batch_eligible_counts,
)
from backend.models.job import Job

if TYPE_CHECKING:
    from backend.kernel.scheduling.job_scheduler import SchedulerNodeSnapshot
    from backend.kernel.policy.types import SolverConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Solver-config accessor
# ---------------------------------------------------------------------------


def _get_solver_config() -> SolverConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.solver


# ---------------------------------------------------------------------------
# PlacementCandidate  (single (job, node) evaluation record)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlacementCandidate:
    """A (job, node) pair evaluated by the solver."""

    job: Job
    node: SchedulerNodeSnapshot
    score: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# PlacementSolver
# ---------------------------------------------------------------------------


class PlacementSolver:
    """Global placement optimiser that considers all (job 脳 node) pairs.

    Unlike per-node ``select_jobs_for_node`` which scores independently,
    this solver builds a constraint matrix and applies a greedy weighted
    bipartite matching with resource accounting:

    1. **Feasibility filter** 鈥?eliminate infeasible (job, node) pairs.
    2. **Scoring** 鈥?per-pair score using the existing ``score_job_for_node``.
    3. **Global adjustments** 鈥?spread, bin-pack, affinity, and locality
       bonuses that account for cross-node state.
    4. **Greedy matching** 鈥?iterate by descending score, assign each job
       to its best node while deducting capacity.

    The solver produces a placement plan:
    ``dict[str, str]`` mapping ``job_id 鈫?node_id``.

    Callers (dispatch cycle) can use the plan as placement hints that
    strongly bias per-node selection without breaking the pull model.
    """

    def solve(  # noqa: C901
        self,
        jobs: list[Job],
        nodes: list[SchedulerNodeSnapshot],
        *,
        now: object,
        accepted_kinds: set[str],
        recent_failed_job_ids: set[str] | None = None,
        active_jobs_by_node: dict[str, list[Job]] | None = None,
        metrics: dict[str, object] | None = None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, str]:
        """Run the global placement solver.

        Returns mapping {job_id: preferred_node_id}.
        """
        # Lazy import: placement_solver is loaded *during* job_scheduler's own
        # module init, so we must not import job_scheduler at module level.
        import datetime as _dt

        from backend.kernel.scheduling.job_scheduler import is_node_eligible, job_matches_node

        _now: _dt.datetime = now  # type: ignore[assignment]

        if metrics is not None:
            metrics.setdefault("solver_invoked", True)
            metrics.setdefault("timed_out", False)
        if not jobs or not nodes:
            if metrics is not None:
                metrics["assignments"] = 0
                metrics["result"] = "empty_window"
            return {}

        live_nodes = [n for n in nodes if is_node_eligible(n, _now)]
        if metrics is not None:
            metrics["live_nodes"] = len(live_nodes)
        if not live_nodes:
            if metrics is not None:
                metrics["assignments"] = 0
                metrics["result"] = "no_live_nodes"
            return {}

        fast_plan = self._solve_large_simple_batch(
            jobs,
            live_nodes,
            now=_now,
            accepted_kinds=accepted_kinds,
            active_jobs_by_node=active_jobs_by_node,
            metrics=metrics,
        )
        if fast_plan is not None:
            return fast_plan

        failed_ids = recent_failed_job_ids or set()
        node_active_jobs = active_jobs_by_node or {}
        total_active = len(live_nodes)

        # 鈹€鈹€ Phase 1: Build feasible candidates 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        candidates: list[PlacementCandidate] = []
        sparse_pairs = 0
        for job in jobs:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if metrics is not None:
                    metrics["timed_out"] = True
                    metrics["assignments"] = 0
                    metrics["result"] = "time_budget_exceeded"
                return {}
            candidate_nodes = _candidate_nodes_for_job(job, live_nodes, accepted_kinds=accepted_kinds)
            sparse_pairs += len(candidate_nodes)
            for node in candidate_nodes:
                if not job_matches_node(job, node, now=_now, accepted_kinds=None):
                    continue
                candidates.append(PlacementCandidate(job=job, node=node))

        if metrics is not None:
            metrics["feasible_pairs"] = len(candidates)
            metrics["candidate_pairs_sparse"] = sparse_pairs
        if not candidates:
            if metrics is not None:
                metrics["assignments"] = 0
                metrics["result"] = "no_feasible_pairs"
            return {}

        # 鈹€鈹€ Phase 2: Score each candidate 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        eligible_cache = batch_eligible_counts(
            jobs,
            live_nodes,
            now=_now,
            accepted_kinds=accepted_kinds,
        )
        for c in candidates:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if metrics is not None:
                    metrics["timed_out"] = True
                    metrics["assignments"] = 0
                    metrics["result"] = "time_budget_exceeded"
                return {}
            ec = eligible_cache.get(c.job.job_id, 1)
            total, breakdown = score_job_for_node(
                c.job,
                c.node,
                now=_now,
                total_active_nodes=total_active,
                eligible_nodes_count=max(ec, 1),
                recent_failed_job_ids=failed_ids,
                active_jobs_on_node=list(node_active_jobs.get(c.node.node_id, [])),
            )
            c.score = total
            c.breakdown = dict(breakdown)

        # 鈹€鈹€ Phase 3: Global adjustments 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        self._apply_global_adjustments(candidates, live_nodes)

        # 鈹€鈹€ Phase 4: Greedy weighted matching 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        plan = self._greedy_match(
            candidates,
            live_nodes,
            deadline_monotonic=deadline_monotonic,
            metrics=metrics,
        )
        if metrics is not None and "result" not in metrics:
            metrics["assignments"] = len(plan)
            metrics["result"] = "planned" if plan else "no_assignments"
        return plan

    def _solve_large_simple_batch(  # noqa: C901
        self,
        jobs: list[Job],
        live_nodes: list[SchedulerNodeSnapshot],
        *,
        now: object,
        accepted_kinds: set[str],
        active_jobs_by_node: dict[str, list[Job]] | None = None,
        metrics: dict[str, object] | None = None,
    ) -> dict[str, str] | None:
        """O(J log N) fast-path assignment for large batches, including mixed workloads.

        Jobs are partitioned into routing groups using ``_job_routing_key`` 鈥?a
        single ``__dict__`` access per job rather than 18+ ``_job_attr`` calls 鈥?        so the homogeneity scan costs ~1 碌s per job instead of ~10 碌s.

        Unlike the previous implementation which required *all* jobs to share
        the same routing contract (aborting on the first mismatch), this version
        handles heterogeneous batches by assigning each routing group
        independently.  A shared global capacity map prevents any node from
        being over-committed across groups.

        Gang jobs within each group are still assigned atomically.
        """
        import datetime as _dt

        _now: _dt.datetime = now  # type: ignore[assignment]

        candidate_pairs = len(jobs) * len(live_nodes)
        if candidate_pairs < 4_096:
            return None
        if active_jobs_by_node:
            return None
        if not jobs or not live_nodes:
            return {}

        # 鈹€鈹€ Phase 1: Partition jobs by routing key in a single O(J) pass 鈹€鈹€鈹€鈹€鈹€
        groups: dict[tuple[object, ...], list[Job]] = {}
        for job in jobs:
            if _has_items_attr(_job_attr(job, "affinity_rules")):
                # Affinity rules need full cross-node scoring; bail to the full solver.
                return None
            key = _job_routing_key(job)
            bucket = groups.get(key)
            if bucket is None:
                groups[key] = [job]
            else:
                bucket.append(job)

        # 鈹€鈹€ Phase 2: Build a shared global capacity map (prevents over-commit) 鈹€
        global_remaining_cap: dict[str, int] = {n.node_id: max(n.max_concurrency - n.active_lease_count, 0) for n in live_nodes}
        node_index: dict[str, SchedulerNodeSnapshot] = {n.node_id: n for n in live_nodes}

        combined_plan: dict[str, str] = {}
        total_feasible_pairs = 0

        # 鈹€鈹€ Phase 3: Assign each routing group independently 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        for _key, group_jobs in groups.items():
            first_job = group_jobs[0]
            if not str(getattr(first_job, "kind", "") or ""):
                continue

            eligible_nodes = _candidate_nodes_for_job(first_job, live_nodes, accepted_kinds=accepted_kinds)
            if not eligible_nodes:
                continue

            # Filter to nodes that still have capacity in the shared map.
            ordered_node_ids: list[str] = [n.node_id for n in eligible_nodes if global_remaining_cap.get(n.node_id, 0) > 0]
            if not ordered_node_ids:
                continue

            total_feasible_pairs += len(group_jobs) * len(eligible_nodes)

            # Order nodes: prefer under-loaded nodes first to spread load.
            # Pre-compute sort keys to avoid repeated dict lookups per comparison.
            node_sort_keys = [
                (global_remaining_cap[nid] / max(node_index[nid].max_concurrency, 1), -float(node_index[nid].reliability_score), nid)
                for nid in ordered_node_ids
            ]
            ordered_node_ids = [nid for _, nid in sorted(zip(node_sort_keys, ordered_node_ids))]

            group_total_remaining = sum(global_remaining_cap[nid] for nid in ordered_node_ids)

            # Build gang/solo assignment units for this group.
            job_units: list[tuple[str | None, list[Job]]] = []
            gang_map: dict[str, list[Job]] = {}
            for job in group_jobs:
                gang_id = _text_attr(_job_attr(job, "gang_id"))
                if not gang_id:
                    job_units.append((None, [job]))
                    continue
                members = gang_map.get(gang_id)
                if members is None:
                    members = []
                    gang_map[gang_id] = members
                    job_units.append((gang_id, members))
                members.append(job)

            # When capacity is tight, prefer high-priority / older jobs.
            if group_total_remaining < len(group_jobs):
                job_units.sort(
                    key=lambda item: (
                        -max(_int_attr(_job_attr(j, "priority")) for j in item[1]),
                        min(getattr(j, "created_at", _now) for j in item[1]),
                        str(item[0] or _job_attr(item[1][0], "job_id") or ""),
                    ),
                )

            rotating_nodes: deque[str] = deque(ordered_node_ids)

            for gang_id, batch_jobs in job_units:
                batch_size = len(batch_jobs)
                if batch_size <= 0:
                    continue
                if group_total_remaining < batch_size:
                    if gang_id:
                        continue
                    break
                if not rotating_nodes:
                    break

                assigned_nodes: list[str] = []
                for _job in batch_jobs:
                    if not rotating_nodes:
                        break
                    nid = rotating_nodes.popleft()
                    assigned_nodes.append(nid)
                    global_remaining_cap[nid] -= 1
                    group_total_remaining -= 1
                    if global_remaining_cap[nid] > 0:
                        rotating_nodes.append(nid)

                if len(assigned_nodes) != batch_size:
                    # Roll back partial assignments for failed gang.
                    for nid in assigned_nodes:
                        global_remaining_cap[nid] = global_remaining_cap.get(nid, 0) + 1
                        group_total_remaining += 1
                        if global_remaining_cap[nid] == 1:
                            rotating_nodes.appendleft(nid)
                    if gang_id:
                        continue
                    break

                for job, nid in zip(batch_jobs, assigned_nodes, strict=False):
                    combined_plan[str(_job_attr(job, "job_id") or "")] = nid

        if metrics is not None:
            metrics["feasible_pairs"] = total_feasible_pairs
            metrics["assignments"] = len(combined_plan)
            metrics["result"] = "fast_path_planned" if combined_plan else "fast_path_no_assignments"
        return combined_plan

    def _apply_global_adjustments(
        self,
        candidates: list[PlacementCandidate],
        live_nodes: list[SchedulerNodeSnapshot],
    ) -> None:
        """Apply cross-node scoring adjustments."""
        # Pre-compute per-node load ratio
        node_load: dict[str, float] = {}
        for n in live_nodes:
            cap = max(n.max_concurrency, 1)
            node_load[n.node_id] = n.active_lease_count / cap

        # Collect per-job candidate counts for spread bonus
        job_node_count: dict[str, int] = {}
        for c in candidates:
            job_node_count[c.job.job_id] = job_node_count.get(c.job.job_id, 0) + 1

        avg_load = sum(node_load.values()) / max(len(node_load), 1)

        _sol = _get_solver_config()
        for c in candidates:
            load = node_load.get(c.node.node_id, 0.0)

            # Spread bonus: prefer under-loaded nodes
            if load < avg_load:
                bonus = int(_sol.spread_bonus * (1 - load))
                c.score += bonus
                c.breakdown["solver_spread"] = bonus

            # Binpack bonus: if job requests many resources, prefer nodes
            # that already have some load (consolidation).
            req_cpu = max(int(getattr(c.job, "required_cpu_cores", 0) or 0), 0)
            if req_cpu == 0 and load > 0.3:
                bonus = int(_sol.binpack_bonus * load)
                c.score += bonus
                c.breakdown["solver_binpack"] = bonus

            # Locality bonus: data-local nodes
            dk = getattr(c.job, "data_locality_key", None)
            if dk and dk in c.node.cached_data_keys:
                c.score += _sol.locality_bonus
                c.breakdown["solver_locality"] = _sol.locality_bonus

    def _greedy_match(
        self,
        candidates: list[PlacementCandidate],
        live_nodes: list[SchedulerNodeSnapshot],
        *,
        deadline_monotonic: float | None = None,
        metrics: dict[str, object] | None = None,
    ) -> dict[str, str]:
        """Greedy descending-score assignment with capacity deduction.

        Gang-aware: jobs sharing a ``gang_id`` are placed atomically. Gang
        candidates are pre-grouped and solved as a unit so the global heap
        only tracks one entry per gang instead of one entry per member.
        """
        remaining_cap: dict[str, int] = {n.node_id: max(n.max_concurrency - n.active_lease_count, 0) for n in live_nodes}

        plan: dict[str, str] = {}
        assigned_jobs: set[str] = set()
        failed_gangs: set[str] = set()

        solo_candidates: list[PlacementCandidate] = []
        gang_candidates: dict[str, list[PlacementCandidate]] = defaultdict(list)
        for candidate in candidates:
            gang_id = _text_attr(_job_attr(candidate.job, "gang_id"))
            if gang_id:
                gang_candidates[gang_id].append(candidate)
            else:
                solo_candidates.append(candidate)

        heap: list[tuple[int, str, str, str]] = []
        for candidate in solo_candidates:
            heap.append((-candidate.score, "job", candidate.job.job_id, candidate.node.node_id))
        for gang_id, grouped_candidates in gang_candidates.items():
            best_score = max(candidate.score for candidate in grouped_candidates)
            heap.append((-best_score, "gang", gang_id, gang_id))
        heapq.heapify(heap)

        solo_by_key = {(candidate.job.job_id, candidate.node.node_id): candidate for candidate in solo_candidates}

        while heap:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if metrics is not None:
                    metrics["timed_out"] = True
                    metrics["assignments"] = 0
                    metrics["result"] = "time_budget_exceeded"
                return {}
            _neg_score, unit_type, primary_key, secondary_key = heapq.heappop(heap)
            if unit_type == "job":
                job_id = primary_key
                node_id = secondary_key
                if job_id in assigned_jobs or remaining_cap.get(node_id, 0) <= 0:
                    continue
                if (job_id, node_id) not in solo_by_key:
                    continue
                plan[job_id] = node_id
                assigned_jobs.add(job_id)
                remaining_cap[node_id] -= 1
                continue

            gang_id = primary_key
            if gang_id in failed_gangs:
                continue
            grouped_candidates = gang_candidates.get(gang_id, [])
            if not grouped_candidates:
                continue
            assignments = self._assign_gang_group(grouped_candidates, remaining_cap)
            if assignments is None:
                failed_gangs.add(gang_id)
                continue
            for job_id, node_id in assignments.items():
                plan[job_id] = node_id
                assigned_jobs.add(job_id)
            logger.debug("gang_placement_committed: gang=%s members=%d", gang_id, len(assignments))

        return plan

    @staticmethod
    def _assign_gang_group(
        candidates: list[PlacementCandidate],
        remaining_cap: dict[str, int],
    ) -> dict[str, str] | None:
        """Assign a gang as a unit using per-job candidate lists."""
        by_job: dict[str, list[PlacementCandidate]] = defaultdict(list)
        for candidate in candidates:
            by_job[candidate.job.job_id].append(candidate)

        ordered_jobs = sorted(
            by_job.items(),
            key=lambda item: (
                len(item[1]),
                -max(candidate.score for candidate in item[1]),
                item[0],
            ),
        )
        local_remaining = dict(remaining_cap)
        assignments: dict[str, str] = {}

        for job_id, job_candidates in ordered_jobs:
            ranked_candidates = sorted(
                job_candidates,
                key=lambda candidate: (-candidate.score, candidate.node.node_id),
            )
            selected_node_id: str | None = None
            for candidate in ranked_candidates:
                node_id = candidate.node.node_id
                if local_remaining.get(node_id, 0) <= 0:
                    continue
                local_remaining[node_id] -= 1
                selected_node_id = node_id
                break
            if selected_node_id is None:
                return None
            assignments[job_id] = selected_node_id

        remaining_cap.clear()
        remaining_cap.update(local_remaining)
        return assignments


# ---------------------------------------------------------------------------
# Module-level singleton and budgeted-plan wrapper
# ---------------------------------------------------------------------------

# Module-level solver singleton
_solver: PlacementSolver | None = None


def get_placement_solver() -> PlacementSolver:
    """Return the process-wide PlacementSolver singleton."""
    global _solver
    if _solver is None:
        _solver = PlacementSolver()
    return _solver


def build_time_budgeted_placement_plan(
    jobs: list[Job],
    nodes: list[SchedulerNodeSnapshot],
    *,
    now: object,
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str] | None = None,
    active_jobs_by_node: dict[str, list[Job]] | None = None,
    decision_context: dict[str, object] | None = None,
) -> dict[str, str]:
    """Run the global solver only when it fits a strict dispatch latency budget."""
    import datetime as _dt

    from backend.kernel.scheduling import job_scheduler as _job_scheduler

    _now: _dt.datetime = now  # type: ignore[assignment]

    compat_get_solver_config = getattr(_job_scheduler, "_get_solver_config", _get_solver_config)
    compat_get_solver = getattr(_job_scheduler, "get_placement_solver", get_placement_solver)

    solver_cfg = compat_get_solver_config()
    if decision_context is not None:
        decision_context.clear()
        decision_context.update(
            {
                "enabled": bool(solver_cfg.enabled_in_dispatch),
                "attempted": False,
                "candidate_jobs": len(jobs),
                "candidate_nodes": len(nodes),
                "candidate_pairs_upper_bound": len(jobs) * len(nodes),
                "dispatch_time_budget_ms": solver_cfg.dispatch_time_budget_ms,
                "timed_out": False,
                "assignments": 0,
            }
        )
    if not solver_cfg.enabled_in_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "disabled"
        return {}
    if not jobs or not nodes:
        if decision_context is not None:
            decision_context["reason"] = "empty_window"
        return {}
    if len(jobs) > solver_cfg.max_jobs_per_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "oversized_job_window"
        return {}
    if len(nodes) > solver_cfg.max_nodes_per_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "oversized_node_window"
        return {}
    if len(jobs) * len(nodes) > solver_cfg.max_candidate_pairs_per_dispatch:
        if decision_context is not None:
            decision_context["reason"] = "oversized_candidate_matrix"
        return {}

    deadline_monotonic = None
    if solver_cfg.dispatch_time_budget_ms > 0:
        deadline_monotonic = time.monotonic() + (solver_cfg.dispatch_time_budget_ms / 1000.0)
    if decision_context is not None:
        decision_context["attempted"] = True
        decision_context["reason"] = "solver_attempted"
    plan = compat_get_solver().solve(
        jobs,
        nodes,
        now=_now,
        accepted_kinds=accepted_kinds,
        recent_failed_job_ids=recent_failed_job_ids,
        active_jobs_by_node=active_jobs_by_node,
        metrics=decision_context,
        deadline_monotonic=deadline_monotonic,
    )
    if decision_context is not None:
        decision_context["assignments"] = len(plan)
        decision_context["reason"] = str(decision_context.get("result", "planned" if plan else "no_assignments"))
    return plan

