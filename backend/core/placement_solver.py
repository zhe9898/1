"""Global Placement Solver — cross-node constraint-satisfaction optimisation.

Extracted from ``job_scheduler.py`` to reduce that module's size.

Dependency contract (no cycles):
    scheduling_candidates  →  placement_solver  →  (lazy runtime) job_scheduler

Runtime imports from ``job_scheduler`` (``is_node_eligible``,
``job_matches_node``) are deferred inside method bodies so this module loads
cleanly even when ``job_scheduler`` has not finished its own import sequence.

All public symbols remain importable from ``backend.core.job_scheduler`` via
re-exports for backward compatibility.
"""

from __future__ import annotations

import heapq
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.core.job_scoring import score_job_for_node
from backend.core.scheduling_candidates import (
    _bool_attr,
    _candidate_nodes_for_job,
    _has_items_attr,
    _int_attr,
    _job_attr,
    _required_capability_set,
    _text_attr,
    batch_eligible_counts,
)
from backend.core.worker_pool import resolve_job_queue_contract_from_record
from backend.models.job import Job

if TYPE_CHECKING:
    from backend.core.job_scheduler import SchedulerNodeSnapshot
    from backend.core.scheduling_policy_types import SolverConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Solver-config accessor
# ---------------------------------------------------------------------------


def _get_solver_config() -> SolverConfig:
    from backend.core.scheduling_policy_store import get_policy_store

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
    """Global placement optimiser that considers all (job × node) pairs.

    Unlike per-node ``select_jobs_for_node`` which scores independently,
    this solver builds a constraint matrix and applies a greedy weighted
    bipartite matching with resource accounting:

    1. **Feasibility filter** — eliminate infeasible (job, node) pairs.
    2. **Scoring** — per-pair score using the existing ``score_job_for_node``.
    3. **Global adjustments** — spread, bin-pack, affinity, and locality
       bonuses that account for cross-node state.
    4. **Greedy matching** — iterate by descending score, assign each job
       to its best node while deducting capacity.

    The solver produces a placement plan:
    ``dict[str, str]`` mapping ``job_id → node_id``.

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

        from backend.core.job_scheduler import is_node_eligible, job_matches_node

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

        # ── Phase 1: Build feasible candidates ───────────────────────
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

        # ── Phase 2: Score each candidate ────────────────────────────
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

        # ── Phase 3: Global adjustments ──────────────────────────────
        self._apply_global_adjustments(candidates, live_nodes)

        # ── Phase 4: Greedy weighted matching ────────────────────────
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
        """Use an O(J log N) assignment path for very large homogeneous batches.

        The default solver builds a full candidate matrix, which is appropriate
        for heterogeneous or gang workloads but unnecessarily expensive for
        large batches of equivalent jobs. This fast path is intentionally
        conservative and only activates when every job shares the same simple
        routing contract so node eligibility can be computed once for the
        entire batch. Large homogeneous gang workloads can also use this path
        as long as gang placement stays atomic.
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

        first_job = jobs[0]
        base_kind = str(getattr(first_job, "kind", "") or "")
        base_queue_class, base_worker_pool = resolve_job_queue_contract_from_record(first_job)
        base_target_os = _text_attr(_job_attr(first_job, "target_os"))
        base_target_arch = _text_attr(_job_attr(first_job, "target_arch"))
        base_target_zone = _text_attr(_job_attr(first_job, "target_zone"))
        base_target_executor = _text_attr(_job_attr(first_job, "target_executor"))
        base_required_capabilities = _required_capability_set(first_job)
        base_required_cpu = max(_int_attr(_job_attr(first_job, "required_cpu_cores")), 0)
        base_required_memory = max(_int_attr(_job_attr(first_job, "required_memory_mb")), 0)
        base_required_gpu = max(_int_attr(_job_attr(first_job, "required_gpu_vram_mb")), 0)
        base_required_storage = max(_int_attr(_job_attr(first_job, "required_storage_mb")), 0)
        base_max_latency = max(_int_attr(_job_attr(first_job, "max_network_latency_ms")), 0)
        base_data_locality_key = _text_attr(_job_attr(first_job, "data_locality_key"))
        base_prefer_cached = _bool_attr(_job_attr(first_job, "prefer_cached_data"))
        base_power_budget = max(_int_attr(_job_attr(first_job, "power_budget_watts")), 0)
        base_thermal_sensitivity = _text_attr(_job_attr(first_job, "thermal_sensitivity"))
        base_cloud_fallback = _bool_attr(_job_attr(first_job, "cloud_fallback_enabled"))
        if not base_kind:
            return None
        if _has_items_attr(_job_attr(first_job, "affinity_rules")):
            return None

        for job in jobs:
            job_kind = _text_attr(_job_attr(job, "kind")) or ""
            requested_queue_class = _text_attr(_job_attr(job, "queue_class"))
            requested_worker_pool = _text_attr(_job_attr(job, "worker_pool"))
            if (
                job_kind != base_kind
                or (requested_queue_class is not None and requested_queue_class.lower() != base_queue_class)
                or (requested_worker_pool is not None and requested_worker_pool.lower() != base_worker_pool)
                or _text_attr(_job_attr(job, "target_os")) != base_target_os
                or _text_attr(_job_attr(job, "target_arch")) != base_target_arch
                or _text_attr(_job_attr(job, "target_zone")) != base_target_zone
                or _text_attr(_job_attr(job, "target_executor")) != base_target_executor
                or _required_capability_set(job) != base_required_capabilities
                or max(_int_attr(_job_attr(job, "required_cpu_cores")), 0) != base_required_cpu
                or max(_int_attr(_job_attr(job, "required_memory_mb")), 0) != base_required_memory
                or max(_int_attr(_job_attr(job, "required_gpu_vram_mb")), 0) != base_required_gpu
                or max(_int_attr(_job_attr(job, "required_storage_mb")), 0) != base_required_storage
                or max(_int_attr(_job_attr(job, "max_network_latency_ms")), 0) != base_max_latency
                or _text_attr(_job_attr(job, "data_locality_key")) != base_data_locality_key
                or _bool_attr(_job_attr(job, "prefer_cached_data")) != base_prefer_cached
                or max(_int_attr(_job_attr(job, "power_budget_watts")), 0) != base_power_budget
                or _text_attr(_job_attr(job, "thermal_sensitivity")) != base_thermal_sensitivity
                or _bool_attr(_job_attr(job, "cloud_fallback_enabled")) != base_cloud_fallback
                or _has_items_attr(_job_attr(job, "affinity_rules"))
            ):
                return None

        eligible_nodes = _candidate_nodes_for_job(first_job, live_nodes, accepted_kinds=accepted_kinds)
        if not eligible_nodes:
            if metrics is not None:
                metrics["assignments"] = 0
                metrics["result"] = "fast_path_no_eligible_nodes"
            return {}

        remaining_cap: dict[str, int] = {}
        total_capacity = 0
        ordered_node_ids: list[str] = []
        for node in eligible_nodes:
            remaining = max(node.max_concurrency - node.active_lease_count, 0)
            if remaining <= 0:
                continue
            remaining_cap[node.node_id] = remaining
            total_capacity += remaining
            ordered_node_ids.append(node.node_id)

        if not ordered_node_ids:
            if metrics is not None:
                metrics["assignments"] = 0
                metrics["result"] = "fast_path_no_capacity"
            return {}

        job_groups: dict[str, list[Job]] = {}
        ordered_units: list[tuple[str | None, list[Job]]] = []
        for job in jobs:
            gang_id = _text_attr(_job_attr(job, "gang_id"))
            if not gang_id:
                ordered_units.append((None, [job]))
                continue
            members = job_groups.get(gang_id)
            if members is None:
                members = []
                job_groups[gang_id] = members
                ordered_units.append((gang_id, members))
            members.append(job)

        if total_capacity < len(jobs):
            ordered_units.sort(
                key=lambda item: (
                    -max(_int_attr(_job_attr(job, "priority")) for job in item[1]),
                    min(getattr(job, "created_at", _now) for job in item[1]),
                    str(item[0] or _job_attr(item[1][0], "job_id") or ""),
                ),
            )

        node_index = {node.node_id: node for node in eligible_nodes}
        ordered_node_ids.sort(
            key=lambda node_id: (
                remaining_cap[node_id] / max(node_index[node_id].max_concurrency, 1),
                -float(node_index[node_id].reliability_score),
                node_id,
            )
        )
        rotating_nodes = deque(ordered_node_ids)
        plan: dict[str, str] = {}
        total_remaining = total_capacity
        for gang_id, batch_jobs in ordered_units:
            batch_size = len(batch_jobs)
            if batch_size <= 0:
                continue
            if total_remaining < batch_size:
                if gang_id:
                    continue
                break
            if not rotating_nodes:
                break

            assigned_nodes: list[str] = []
            for _job in batch_jobs:
                if not rotating_nodes:
                    break
                node_id = rotating_nodes.popleft()
                assigned_nodes.append(node_id)
                remaining_cap[node_id] -= 1
                total_remaining -= 1
                if remaining_cap[node_id] > 0:
                    rotating_nodes.append(node_id)

            if len(assigned_nodes) != batch_size:
                for node_id in assigned_nodes:
                    remaining_cap[node_id] = remaining_cap.get(node_id, 0) + 1
                    total_remaining += 1
                    if remaining_cap[node_id] == 1:
                        rotating_nodes.appendleft(node_id)
                if gang_id:
                    continue
                break

            for job, node_id in zip(batch_jobs, assigned_nodes, strict=False):
                plan[str(_job_attr(job, "job_id") or "")] = node_id

        if metrics is not None:
            metrics["feasible_pairs"] = len(jobs) * len(eligible_nodes)
            metrics["assignments"] = len(plan)
            metrics["result"] = "fast_path_planned" if plan else "fast_path_no_assignments"
        return plan

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

            # Binpack bonus: if the job has no CPU requirement (tiny job), prefer
            # nodes that already have some load to consolidate small jobs and keep
            # other nodes free for resource-heavy work.
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

    _now: _dt.datetime = now  # type: ignore[assignment]

    solver_cfg = _get_solver_config()
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
    plan = get_placement_solver().solve(
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
