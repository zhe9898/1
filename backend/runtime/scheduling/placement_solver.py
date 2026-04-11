"""Global placement solver for cross-node scheduling decisions.

This module stays intentionally self-contained because it is imported from the
pull-dispatch path and from ``job_scheduler``. Runtime helpers from
``job_scheduler`` are still imported lazily inside ``solve`` to avoid import
cycles during module initialization.
"""

from __future__ import annotations

import datetime
import heapq
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from backend.models.job import Job
from backend.runtime.scheduling.job_scoring import score_job_for_node
from backend.runtime.scheduling.scheduling_candidates import (
    _candidate_nodes_for_job,
    _has_items_attr,
    _int_attr,
    _job_attr,
    _job_routing_key,
    _text_attr,
    batch_eligible_counts,
)
from backend.runtime.scheduling.worker_pool import resolve_job_queue_contract_from_record

if TYPE_CHECKING:
    from backend.kernel.policy.types import SolverConfig
    from backend.runtime.scheduling.job_scheduler import SchedulerNodeSnapshot

logger = logging.getLogger(__name__)


def _routing_group_node_score(
    node: SchedulerNodeSnapshot,
    job: Job,
    remaining_capacity: int,
    *,
    binpack: bool,
) -> float:
    """Mirror the Go fast-path node ranking so fallback placement stays aligned."""
    capacity = max(node.max_concurrency, 1)
    load_ratio = remaining_capacity / capacity

    if binpack:
        base = (1.0 - load_ratio) * 10.0
    else:
        base = load_ratio * 10.0
    base += float(node.reliability_score)

    data_locality_key = _text_attr(_job_attr(job, "data_locality_key"))
    if data_locality_key and data_locality_key in node.cached_data_keys:
        base += 3.0

    thermal_sensitivity = _text_attr(_job_attr(job, "thermal_sensitivity"))
    if thermal_sensitivity == "high":
        if node.thermal_state == "cool":
            base += 2.0
        elif node.thermal_state in {"hot", "throttling"}:
            base -= 2.0

    max_latency_ms = max(_int_attr(_job_attr(job, "max_network_latency_ms")), 0)
    if max_latency_ms > 0 and node.network_latency_ms > 0 and node.network_latency_ms * 2 <= max_latency_ms:
        base += 1.0

    return base


def _get_solver_config() -> SolverConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.solver


@dataclass(slots=True)
class PlacementCandidate:
    """A scored (job, node) pair."""

    job: Job
    node: SchedulerNodeSnapshot
    score: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)


def _metrics_update(metrics: dict[str, object] | None, **updates: object) -> None:
    if metrics is not None:
        metrics.update(updates)


def _deadline_exceeded(deadline_monotonic: float | None) -> bool:
    return deadline_monotonic is not None and time.monotonic() >= deadline_monotonic


def _build_timeout_plan(metrics: dict[str, object] | None) -> dict[str, str]:
    _metrics_update(
        metrics,
        timed_out=True,
        assignments=0,
        result="time_budget_exceeded",
    )
    return {}


def _build_empty_plan(metrics: dict[str, object] | None, *, result: str) -> dict[str, str]:
    _metrics_update(metrics, assignments=0, result=result)
    return {}


class PlacementSolver:
    """Global placement optimizer across all candidate jobs and nodes."""

    def solve(
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
        """Return a preferred-node plan as ``{job_id: node_id}``."""
        import datetime as _dt

        from backend.runtime.scheduling.job_scheduler import is_node_eligible, job_matches_node

        _now: _dt.datetime = now  # type: ignore[assignment]

        if metrics is not None:
            metrics.setdefault("solver_invoked", True)
            metrics.setdefault("timed_out", False)
        if not jobs or not nodes:
            return _build_empty_plan(metrics, result="empty_window")

        live_nodes = self._eligible_live_nodes(nodes, now=_now, metrics=metrics, is_node_eligible=is_node_eligible)
        if not live_nodes:
            return _build_empty_plan(metrics, result="no_live_nodes")

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
        candidates, sparse_pairs = self._build_feasible_candidates(
            jobs,
            live_nodes,
            now=_now,
            accepted_kinds=accepted_kinds,
            deadline_monotonic=deadline_monotonic,
            job_matches_node=job_matches_node,
        )
        if candidates is None:
            return _build_timeout_plan(metrics)
        _metrics_update(metrics, feasible_pairs=len(candidates), candidate_pairs_sparse=sparse_pairs)
        if not candidates:
            return _build_empty_plan(metrics, result="no_feasible_pairs")

        eligible_cache = batch_eligible_counts(
            jobs,
            live_nodes,
            now=_now,
            accepted_kinds=accepted_kinds,
        )
        scored_candidates = self._score_candidates(
            candidates,
            now=_now,
            total_active=len(live_nodes),
            eligible_cache=eligible_cache,
            failed_ids=failed_ids,
            node_active_jobs=node_active_jobs,
            deadline_monotonic=deadline_monotonic,
        )
        if scored_candidates is None:
            return _build_timeout_plan(metrics)

        self._apply_global_adjustments(scored_candidates, live_nodes)
        plan = self._greedy_match(
            scored_candidates,
            live_nodes,
            deadline_monotonic=deadline_monotonic,
            metrics=metrics,
        )
        if metrics is not None and "result" not in metrics:
            _metrics_update(
                metrics,
                assignments=len(plan),
                result="planned" if plan else "no_assignments",
            )
        return plan

    @staticmethod
    def _eligible_live_nodes(
        nodes: list[SchedulerNodeSnapshot],
        *,
        now: object,
        metrics: dict[str, object] | None,
        is_node_eligible: Callable[..., bool],
    ) -> list[SchedulerNodeSnapshot]:
        live_nodes = [node for node in nodes if is_node_eligible(node, now)]
        _metrics_update(metrics, live_nodes=len(live_nodes))
        return live_nodes

    @staticmethod
    def _build_feasible_candidates(
        jobs: list[Job],
        live_nodes: list[SchedulerNodeSnapshot],
        *,
        now: object,
        accepted_kinds: set[str],
        deadline_monotonic: float | None,
        job_matches_node: Callable[..., bool],
    ) -> tuple[list[PlacementCandidate] | None, int]:
        candidates: list[PlacementCandidate] = []
        sparse_pairs = 0
        for job in jobs:
            if _deadline_exceeded(deadline_monotonic):
                return None, sparse_pairs
            candidate_nodes = _candidate_nodes_for_job(job, live_nodes, accepted_kinds=accepted_kinds)
            sparse_pairs += len(candidate_nodes)
            for node in candidate_nodes:
                if not job_matches_node(job, node, now=now, accepted_kinds=None):
                    continue
                candidates.append(PlacementCandidate(job=job, node=node))
        return candidates, sparse_pairs

    @staticmethod
    def _score_candidates(
        candidates: list[PlacementCandidate],
        *,
        now: datetime.datetime,
        total_active: int,
        eligible_cache: dict[str, int],
        failed_ids: set[str],
        node_active_jobs: dict[str, list[Job]],
        deadline_monotonic: float | None,
    ) -> list[PlacementCandidate] | None:
        for candidate in candidates:
            if _deadline_exceeded(deadline_monotonic):
                return None
            eligible_count = max(eligible_cache.get(candidate.job.job_id, 1), 1)
            total, breakdown = score_job_for_node(
                candidate.job,
                candidate.node,
                now=now,
                total_active_nodes=total_active,
                eligible_nodes_count=eligible_count,
                recent_failed_job_ids=failed_ids,
                active_jobs_on_node=list(node_active_jobs.get(candidate.node.node_id, [])),
            )
            candidate.score = total
            candidate.breakdown = dict(breakdown)
        return candidates

    def _solve_large_simple_batch(
        self,
        jobs: list[Job],
        live_nodes: list[SchedulerNodeSnapshot],
        *,
        now: datetime.datetime,
        accepted_kinds: set[str],
        active_jobs_by_node: dict[str, list[Job]] | None = None,
        metrics: dict[str, object] | None = None,
    ) -> dict[str, str] | None:
        """O(J log N) fast-path assignment for large homogeneous routing groups."""
        candidate_pairs = len(jobs) * len(live_nodes)
        if not self._can_use_large_simple_batch(
            jobs,
            live_nodes,
            candidate_pairs=candidate_pairs,
            active_jobs_by_node=active_jobs_by_node,
        ):
            return None

        groups = self._partition_routing_groups(jobs)
        if groups is None:
            return None

        global_remaining_cap = self._build_global_remaining_capacity(live_nodes)
        node_index = {node.node_id: node for node in live_nodes}
        combined_plan: dict[str, str] = {}
        total_feasible_pairs = 0

        for group_jobs in groups.values():
            group_plan, feasible_pairs = self._solve_large_simple_group(
                group_jobs,
                live_nodes,
                now=now,
                accepted_kinds=accepted_kinds,
                global_remaining_cap=global_remaining_cap,
                node_index=node_index,
            )
            total_feasible_pairs += feasible_pairs
            combined_plan.update(group_plan)

        _metrics_update(
            metrics,
            feasible_pairs=total_feasible_pairs,
            assignments=len(combined_plan),
            result="fast_path_planned" if combined_plan else "fast_path_no_assignments",
        )
        return combined_plan

    @staticmethod
    def _can_use_large_simple_batch(
        jobs: list[Job],
        live_nodes: list[SchedulerNodeSnapshot],
        *,
        candidate_pairs: int,
        active_jobs_by_node: dict[str, list[Job]] | None,
    ) -> bool:
        if candidate_pairs < 4_096:
            return False
        if active_jobs_by_node:
            return False
        return bool(jobs and live_nodes)

    @staticmethod
    def _partition_routing_groups(jobs: list[Job]) -> dict[tuple[object, ...], list[Job]] | None:
        groups: dict[tuple[object, ...], list[Job]] = {}
        for job in jobs:
            if _has_items_attr(_job_attr(job, "affinity_rules")):
                return None
            groups.setdefault(_job_routing_key(job), []).append(job)
        return groups

    @staticmethod
    def _build_global_remaining_capacity(live_nodes: list[SchedulerNodeSnapshot]) -> dict[str, int]:
        return {node.node_id: max(node.max_concurrency - node.active_lease_count, 0) for node in live_nodes}

    def _solve_large_simple_group(
        self,
        group_jobs: list[Job],
        live_nodes: list[SchedulerNodeSnapshot],
        *,
        now: datetime.datetime,
        accepted_kinds: set[str],
        global_remaining_cap: dict[str, int],
        node_index: dict[str, SchedulerNodeSnapshot],
    ) -> tuple[dict[str, str], int]:
        first_job = group_jobs[0]
        if not str(getattr(first_job, "kind", "") or ""):
            return {}, 0

        eligible_nodes = _candidate_nodes_for_job(first_job, live_nodes, accepted_kinds=accepted_kinds)
        if not eligible_nodes:
            return {}, 0

        queue_class, _worker_pool = resolve_job_queue_contract_from_record(first_job)
        ordered_node_ids = self._rank_fast_path_nodes(
            first_job,
            eligible_nodes,
            global_remaining_cap=global_remaining_cap,
            node_index=node_index,
            binpack=queue_class == "batch",
        )
        if not ordered_node_ids:
            return {}, 0

        feasible_pairs = len(group_jobs) * len(eligible_nodes)
        group_jobs.sort(key=lambda job: -_int_attr(_job_attr(job, "priority")))
        job_units = self._build_fast_path_job_units(group_jobs)
        self._prioritize_fast_path_job_units(
            job_units,
            now=now,
            remaining_capacity=sum(global_remaining_cap[node_id] for node_id in ordered_node_ids),
            total_jobs=len(group_jobs),
        )
        return (
            self._assign_fast_path_job_units(
                job_units,
                ordered_node_ids=ordered_node_ids,
                global_remaining_cap=global_remaining_cap,
            ),
            feasible_pairs,
        )

    @staticmethod
    def _rank_fast_path_nodes(
        first_job: Job,
        eligible_nodes: list[SchedulerNodeSnapshot],
        *,
        global_remaining_cap: dict[str, int],
        node_index: dict[str, SchedulerNodeSnapshot],
        binpack: bool,
    ) -> list[str]:
        ordered_node_ids = [node.node_id for node in eligible_nodes if global_remaining_cap.get(node.node_id, 0) > 0]
        ordered_node_ids.sort(
            key=lambda node_id: (
                -_routing_group_node_score(
                    node_index[node_id],
                    first_job,
                    global_remaining_cap[node_id],
                    binpack=binpack,
                ),
                node_id,
            )
        )
        return ordered_node_ids

    @staticmethod
    def _build_fast_path_job_units(group_jobs: list[Job]) -> list[tuple[str | None, list[Job]]]:
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
        return job_units

    @staticmethod
    def _prioritize_fast_path_job_units(
        job_units: list[tuple[str | None, list[Job]]],
        *,
        now: datetime.datetime,
        remaining_capacity: int,
        total_jobs: int,
    ) -> None:
        if remaining_capacity >= total_jobs:
            return
        job_units.sort(
            key=lambda item: (
                -max(_int_attr(_job_attr(job, "priority")) for job in item[1]),
                min(getattr(job, "created_at", now) for job in item[1]),
                str(item[0] or _job_attr(item[1][0], "job_id") or ""),
            ),
        )

    @staticmethod
    def _assign_fast_path_job_units(
        job_units: list[tuple[str | None, list[Job]]],
        *,
        ordered_node_ids: list[str],
        global_remaining_cap: dict[str, int],
    ) -> dict[str, str]:
        plan: dict[str, str] = {}
        rotating_nodes: deque[str] = deque(ordered_node_ids)
        group_total_remaining = sum(global_remaining_cap[node_id] for node_id in ordered_node_ids)

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

            assigned_nodes = PlacementSolver._assign_fast_path_batch(
                batch_jobs,
                rotating_nodes=rotating_nodes,
                global_remaining_cap=global_remaining_cap,
            )
            if assigned_nodes is None:
                if gang_id:
                    continue
                break

            group_total_remaining -= len(assigned_nodes)
            for job, node_id in zip(batch_jobs, assigned_nodes, strict=False):
                plan[str(_job_attr(job, "job_id") or "")] = node_id

        return plan

    @staticmethod
    def _assign_fast_path_batch(
        batch_jobs: list[Job],
        *,
        rotating_nodes: deque[str],
        global_remaining_cap: dict[str, int],
    ) -> list[str] | None:
        assigned_nodes: list[str] = []
        for _job in batch_jobs:
            if not rotating_nodes:
                break
            node_id = rotating_nodes.popleft()
            assigned_nodes.append(node_id)
            global_remaining_cap[node_id] -= 1
            if global_remaining_cap[node_id] > 0:
                rotating_nodes.append(node_id)

        if len(assigned_nodes) == len(batch_jobs):
            return assigned_nodes

        PlacementSolver._rollback_fast_path_batch(
            assigned_nodes,
            rotating_nodes=rotating_nodes,
            global_remaining_cap=global_remaining_cap,
        )
        return None

    @staticmethod
    def _rollback_fast_path_batch(
        assigned_nodes: list[str],
        *,
        rotating_nodes: deque[str],
        global_remaining_cap: dict[str, int],
    ) -> None:
        for node_id in assigned_nodes:
            global_remaining_cap[node_id] = global_remaining_cap.get(node_id, 0) + 1
            if global_remaining_cap[node_id] == 1:
                rotating_nodes.appendleft(node_id)

    def _apply_global_adjustments(
        self,
        candidates: list[PlacementCandidate],
        live_nodes: list[SchedulerNodeSnapshot],
    ) -> None:
        """Apply cross-node scoring adjustments."""
        node_load: dict[str, float] = {}
        for node in live_nodes:
            capacity = max(node.max_concurrency, 1)
            node_load[node.node_id] = node.active_lease_count / capacity

        avg_load = sum(node_load.values()) / max(len(node_load), 1)
        solver_cfg = _get_solver_config()
        for candidate in candidates:
            load = node_load.get(candidate.node.node_id, 0.0)

            if load < avg_load:
                bonus = int(solver_cfg.spread_bonus * (1 - load))
                candidate.score += bonus
                candidate.breakdown["solver_spread"] = bonus

            req_cpu = max(int(getattr(candidate.job, "required_cpu_cores", 0) or 0), 0)
            if req_cpu == 0 and load > 0.3:
                bonus = int(solver_cfg.binpack_bonus * load)
                candidate.score += bonus
                candidate.breakdown["solver_binpack"] = bonus

            data_locality_key = getattr(candidate.job, "data_locality_key", None)
            if data_locality_key and data_locality_key in candidate.node.cached_data_keys:
                candidate.score += solver_cfg.locality_bonus
                candidate.breakdown["solver_locality"] = solver_cfg.locality_bonus

    @staticmethod
    def _partition_match_units(
        candidates: list[PlacementCandidate],
    ) -> tuple[list[PlacementCandidate], dict[str, list[PlacementCandidate]]]:
        solo_candidates: list[PlacementCandidate] = []
        gang_candidates: dict[str, list[PlacementCandidate]] = defaultdict(list)
        for candidate in candidates:
            gang_id = _text_attr(_job_attr(candidate.job, "gang_id"))
            if gang_id:
                gang_candidates[gang_id].append(candidate)
            else:
                solo_candidates.append(candidate)
        return solo_candidates, gang_candidates

    @staticmethod
    def _build_match_heap(
        solo_candidates: list[PlacementCandidate],
        gang_candidates: dict[str, list[PlacementCandidate]],
    ) -> list[tuple[int, str, str, str]]:
        heap: list[tuple[int, str, str, str]] = [(-candidate.score, "job", candidate.job.job_id, candidate.node.node_id) for candidate in solo_candidates]
        for gang_id, grouped_candidates in gang_candidates.items():
            heap.append((-max(candidate.score for candidate in grouped_candidates), "gang", gang_id, gang_id))
        heapq.heapify(heap)
        return heap

    @staticmethod
    def _apply_solo_assignment(
        *,
        job_id: str,
        node_id: str,
        assigned_jobs: set[str],
        remaining_cap: dict[str, int],
        solo_by_key: dict[tuple[str, str], PlacementCandidate],
        plan: dict[str, str],
    ) -> bool:
        if job_id in assigned_jobs or remaining_cap.get(node_id, 0) <= 0:
            return False
        if (job_id, node_id) not in solo_by_key:
            return False
        plan[job_id] = node_id
        assigned_jobs.add(job_id)
        remaining_cap[node_id] -= 1
        return True

    def _apply_gang_assignment(
        self,
        *,
        gang_id: str,
        gang_candidates: dict[str, list[PlacementCandidate]],
        failed_gangs: set[str],
        assigned_jobs: set[str],
        remaining_cap: dict[str, int],
        plan: dict[str, str],
    ) -> bool:
        if gang_id in failed_gangs:
            return False
        grouped_candidates = gang_candidates.get(gang_id, [])
        if not grouped_candidates:
            return False
        assignments = self._assign_gang_group(grouped_candidates, remaining_cap)
        if assignments is None:
            failed_gangs.add(gang_id)
            return False
        for job_id, node_id in assignments.items():
            plan[job_id] = node_id
            assigned_jobs.add(job_id)
        logger.debug("gang_placement_committed: gang=%s members=%d", gang_id, len(assignments))
        return True

    def _greedy_match(
        self,
        candidates: list[PlacementCandidate],
        live_nodes: list[SchedulerNodeSnapshot],
        *,
        deadline_monotonic: float | None = None,
        metrics: dict[str, object] | None = None,
    ) -> dict[str, str]:
        """Greedy descending-score assignment with gang-aware capacity handling."""
        remaining_cap: dict[str, int] = {node.node_id: max(node.max_concurrency - node.active_lease_count, 0) for node in live_nodes}
        plan: dict[str, str] = {}
        assigned_jobs: set[str] = set()
        failed_gangs: set[str] = set()
        solo_candidates, gang_candidates = self._partition_match_units(candidates)
        heap = self._build_match_heap(solo_candidates, gang_candidates)
        solo_by_key = {(candidate.job.job_id, candidate.node.node_id): candidate for candidate in solo_candidates}

        while heap:
            if _deadline_exceeded(deadline_monotonic):
                return _build_timeout_plan(metrics)

            _neg_score, unit_type, primary_key, secondary_key = heapq.heappop(heap)
            if unit_type == "job":
                self._apply_solo_assignment(
                    job_id=primary_key,
                    node_id=secondary_key,
                    assigned_jobs=assigned_jobs,
                    remaining_cap=remaining_cap,
                    solo_by_key=solo_by_key,
                    plan=plan,
                )
                continue
            self._apply_gang_assignment(
                gang_id=primary_key,
                gang_candidates=gang_candidates,
                failed_gangs=failed_gangs,
                assigned_jobs=assigned_jobs,
                remaining_cap=remaining_cap,
                plan=plan,
            )

        return plan

    @staticmethod
    def _assign_gang_group(
        candidates: list[PlacementCandidate],
        remaining_cap: dict[str, int],
    ) -> dict[str, str] | None:
        """Assign a gang atomically using per-job candidate rankings."""
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


_solver: PlacementSolver | None = None


def get_placement_solver() -> PlacementSolver:
    """Return the process-wide solver singleton."""
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
    """Run the solver only when it fits the dispatch latency budget."""
    import datetime as _dt

    _now: _dt.datetime = now  # type: ignore[assignment]
    solver_cfg = _get_solver_config()

    _initialize_decision_context(
        decision_context,
        solver_cfg=solver_cfg,
        job_count=len(jobs),
        node_count=len(nodes),
    )
    effective_budget_ms = _effective_solver_dispatch_budget_ms(
        solver_cfg,
        candidate_pairs=len(jobs) * len(nodes),
    )
    if decision_context is not None and effective_budget_ms is not None:
        decision_context["dispatch_time_budget_ms"] = effective_budget_ms
    skip_reason = _solver_dispatch_skip_reason(solver_cfg, jobs=jobs, nodes=nodes)
    if skip_reason is not None:
        if decision_context is not None:
            decision_context["reason"] = skip_reason
        return {}

    _warm_solver_dependencies()

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
        deadline_monotonic=_solver_dispatch_deadline(effective_budget_ms),
    )
    if decision_context is not None:
        decision_context["assignments"] = len(plan)
        decision_context["reason"] = str(decision_context.get("result", "planned" if plan else "no_assignments"))
    return plan


def _initialize_decision_context(
    decision_context: dict[str, object] | None,
    *,
    solver_cfg: SolverConfig,
    job_count: int,
    node_count: int,
) -> None:
    if decision_context is None:
        return
    decision_context.clear()
    decision_context.update(
        {
            "enabled": bool(solver_cfg.enabled_in_dispatch),
            "attempted": False,
            "candidate_jobs": job_count,
            "candidate_nodes": node_count,
            "candidate_pairs_upper_bound": job_count * node_count,
            "dispatch_time_budget_ms": solver_cfg.dispatch_time_budget_ms,
            "timed_out": False,
            "assignments": 0,
        }
    )


def _solver_dispatch_skip_reason(
    solver_cfg: SolverConfig,
    *,
    jobs: list[Job],
    nodes: list[SchedulerNodeSnapshot],
) -> str | None:
    if not solver_cfg.enabled_in_dispatch:
        return "disabled"
    if not jobs or not nodes:
        return "empty_window"
    if len(jobs) > solver_cfg.max_jobs_per_dispatch:
        return "oversized_job_window"
    if len(nodes) > solver_cfg.max_nodes_per_dispatch:
        return "oversized_node_window"
    if len(jobs) * len(nodes) > solver_cfg.max_candidate_pairs_per_dispatch:
        return "oversized_candidate_matrix"
    return None


def _warm_solver_dependencies() -> None:
    """Load cached scoring/policy helpers before starting the dispatch deadline."""
    from backend.runtime.scheduling.job_scoring import _get_freshness_policy, _get_scoring_weights
    from backend.runtime.scheduling.placement_policy import get_placement_policy

    _get_scoring_weights()
    _get_freshness_policy()
    get_placement_policy()


def _effective_solver_dispatch_budget_ms(
    solver_cfg: SolverConfig,
    *,
    candidate_pairs: int,
) -> float | None:
    budget_ms = float(solver_cfg.dispatch_time_budget_ms)
    if budget_ms <= 0:
        return None
    if candidate_pairs <= 32:
        # Small windows are latency-safe but still pay one-time module warmup costs.
        budget_ms = max(budget_ms, 25.0)
    return budget_ms


def _solver_dispatch_deadline(dispatch_budget_ms: float | None) -> float | None:
    if dispatch_budget_ms is None:
        return None
    return time.monotonic() + (dispatch_budget_ms / 1000.0)
