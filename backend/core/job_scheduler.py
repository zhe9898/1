from __future__ import annotations

import datetime
import heapq
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.scheduling_policy_types import SolverConfig

from backend.core.job_scoring import (  # noqa: F401 – re-export
    _stable_tiebreak,
    score_job_for_node,
)
from backend.core.worker_pool import infer_node_worker_pools, resolve_job_queue_contract_from_record
from backend.models.job import Job
from backend.models.node import Node

logger = logging.getLogger(__name__)


def _node_stale_seconds() -> int:
    from backend.core.scheduling_policy_store import get_policy_store

    return get_policy_store().active.freshness.stale_after_seconds


def _text_attr(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _has_items_attr(value: object) -> bool:
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return bool(value)
    return False


def _int_attr(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _bool_attr(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _job_attr(job: Job, name: str) -> object:
    raw_state = getattr(job, "__dict__", None)
    if isinstance(raw_state, dict):
        return raw_state.get(name)
    return getattr(job, name, None)


def _required_capability_set(job: Job) -> frozenset[str]:
    raw = _job_attr(job, "required_capabilities")
    if isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in raw if isinstance(item, str) and item.strip())
    return frozenset()


def _candidate_nodes_for_job(
    job: Job,
    live_nodes: list[SchedulerNodeSnapshot],
    *,
    accepted_kinds: set[str] | None = None,
) -> list[SchedulerNodeSnapshot]:
    required_capabilities = _required_capability_set(job)
    required_executor = _text_attr(_job_attr(job, "target_executor"))
    required_os = _text_attr(_job_attr(job, "target_os"))
    required_arch = _text_attr(_job_attr(job, "target_arch"))
    required_zone = _text_attr(_job_attr(job, "target_zone"))
    required_cpu = max(_int_attr(_job_attr(job, "required_cpu_cores")), 0)
    required_memory = max(_int_attr(_job_attr(job, "required_memory_mb")), 0)
    required_gpu = max(_int_attr(_job_attr(job, "required_gpu_vram_mb")), 0)
    required_storage = max(_int_attr(_job_attr(job, "required_storage_mb")), 0)
    required_latency = max(_int_attr(_job_attr(job, "max_network_latency_ms")), 0)
    data_locality_key = _text_attr(_job_attr(job, "data_locality_key"))
    prefer_cached = _bool_attr(_job_attr(job, "prefer_cached_data"))
    power_budget = max(_int_attr(_job_attr(job, "power_budget_watts")), 0)
    thermal_sensitivity = _text_attr(_job_attr(job, "thermal_sensitivity"))
    cloud_fallback = _bool_attr(_job_attr(job, "cloud_fallback_enabled"))
    _queue_class, worker_pool = resolve_job_queue_contract_from_record(job)

    candidate_nodes: list[SchedulerNodeSnapshot] = []
    for node in live_nodes:
        if node.accepted_kinds:
            if job.kind not in node.accepted_kinds:
                continue
        elif accepted_kinds and job.kind not in accepted_kinds:
            continue
        if node.worker_pools and worker_pool not in node.worker_pools:
            continue
        if required_os and node.os != required_os:
            continue
        if required_arch and node.arch != required_arch:
            continue
        if required_executor and node.executor != required_executor:
            continue
        if required_zone and node.zone != required_zone:
            continue
        if required_capabilities and not required_capabilities.issubset(node.capabilities):
            continue
        if required_cpu and node.cpu_cores < required_cpu:
            continue
        if required_memory and node.memory_mb < required_memory:
            continue
        if required_gpu and node.gpu_vram_mb < required_gpu:
            continue
        if required_storage and node.storage_mb < required_storage:
            continue
        if required_latency and node.network_latency_ms > 0 and node.network_latency_ms > required_latency:
            continue
        if data_locality_key and prefer_cached and data_locality_key not in node.cached_data_keys:
            continue
        if power_budget and node.power_capacity_watts > 0:
            available_power = node.power_capacity_watts - node.current_power_watts
            if available_power < power_budget:
                continue
        if thermal_sensitivity == "high" and node.thermal_state in ("hot", "throttling"):
            continue
        if not cloud_fallback and node.cloud_connectivity == "offline":
            continue
        candidate_nodes.append(node)

    return candidate_nodes


@dataclass(slots=True)
class SchedulerNodeSnapshot:
    node_id: str
    os: str
    arch: str
    executor: str
    zone: str | None
    capabilities: frozenset[str]
    accepted_kinds: frozenset[str]
    max_concurrency: int
    active_lease_count: int
    cpu_cores: int
    memory_mb: int
    gpu_vram_mb: int
    storage_mb: int
    reliability_score: float
    last_seen_at: datetime.datetime
    enrollment_status: str
    status: str
    drain_status: str
    # Edge computing attributes
    network_latency_ms: int
    bandwidth_mbps: int
    cached_data_keys: frozenset[str]
    power_capacity_watts: int
    current_power_watts: int
    thermal_state: str
    cloud_connectivity: str
    metadata_json: dict[str, object]
    worker_pools: frozenset[str] = field(default_factory=frozenset)
    tenant_id: str = "default"


@dataclass(slots=True)
class ScoredJob:
    job: Job
    score: int
    eligible_nodes_count: int
    score_breakdown: dict[str, int] | None = None


def build_node_snapshot(node: Node, *, active_lease_count: int, reliability_score: float) -> SchedulerNodeSnapshot:
    return SchedulerNodeSnapshot(
        node_id=node.node_id,
        os=node.os,
        arch=node.arch,
        executor=node.executor,
        zone=node.zone,
        capabilities=frozenset(node.capabilities or []),
        accepted_kinds=frozenset(getattr(node, "accepted_kinds", None) or []),
        worker_pools=frozenset(
            infer_node_worker_pools(
                worker_pools=getattr(node, "worker_pools", None),
                accepted_kinds=getattr(node, "accepted_kinds", None),
                capabilities=node.capabilities,
                gpu_vram_mb=node.gpu_vram_mb,
                profile=node.profile,
                metadata=dict(getattr(node, "metadata_json", None) or {}),
            )
        ),
        max_concurrency=max(int(node.max_concurrency or 1), 1),
        active_lease_count=active_lease_count,
        cpu_cores=max(int(node.cpu_cores or 0), 0),
        memory_mb=max(int(node.memory_mb or 0), 0),
        gpu_vram_mb=max(int(node.gpu_vram_mb or 0), 0),
        storage_mb=max(int(node.storage_mb or 0), 0),
        reliability_score=reliability_score,
        last_seen_at=node.last_seen_at,
        enrollment_status=node.enrollment_status,
        status=node.status,
        drain_status=node.drain_status or "active",
        # Edge computing attributes
        network_latency_ms=max(int(getattr(node, "network_latency_ms", None) or 0), 0),
        bandwidth_mbps=max(int(getattr(node, "bandwidth_mbps", None) or 0), 0),
        cached_data_keys=frozenset(getattr(node, "cached_data_keys", None) or []),
        power_capacity_watts=max(int(getattr(node, "power_capacity_watts", None) or 0), 0),
        current_power_watts=max(int(getattr(node, "current_power_watts", None) or 0), 0),
        thermal_state=str(getattr(node, "thermal_state", None) or "normal"),
        cloud_connectivity=str(getattr(node, "cloud_connectivity", None) or "unknown"),
        metadata_json=dict(getattr(node, "metadata_json", None) or {}),
        tenant_id=str(getattr(node, "tenant_id", "default") or "default"),
    )


def is_node_eligible(node: SchedulerNodeSnapshot, now: datetime.datetime) -> bool:
    if node.enrollment_status != "active":
        return False
    if node.status != "online":
        return False
    if node.drain_status != "active":
        return False
    if node.active_lease_count >= max(node.max_concurrency, 1):
        return False
    return (now - node.last_seen_at).total_seconds() <= _node_stale_seconds()


def _resource_blockers(job: Job, node: SchedulerNodeSnapshot) -> list[str]:
    blockers: list[str] = []
    required_executor = (getattr(job, "target_executor", None) or "").strip()
    if required_executor and required_executor != node.executor:
        blockers.append(f"executor!={required_executor}")
    required_cpu = max(int(getattr(job, "required_cpu_cores", 0) or 0), 0)
    if required_cpu and node.cpu_cores < required_cpu:
        blockers.append(f"cpu<{required_cpu}")
    required_memory = max(int(getattr(job, "required_memory_mb", 0) or 0), 0)
    if required_memory and node.memory_mb < required_memory:
        blockers.append(f"memory<{required_memory}")
    required_gpu = max(int(getattr(job, "required_gpu_vram_mb", 0) or 0), 0)
    if required_gpu and node.gpu_vram_mb < required_gpu:
        blockers.append(f"gpu<{required_gpu}")
    required_storage = max(int(getattr(job, "required_storage_mb", 0) or 0), 0)
    if required_storage and node.storage_mb < required_storage:
        blockers.append(f"storage<{required_storage}")
    return blockers


def node_blockers_for_job(  # noqa: C901
    job: Job,
    node: SchedulerNodeSnapshot,
    *,
    now: datetime.datetime,
    accepted_kinds: set[str] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if node.enrollment_status != "active":
        blockers.append(f"enrollment={node.enrollment_status}")
    if node.status != "online":
        blockers.append(f"status={node.status}")
    if node.drain_status != "active":
        blockers.append(f"drain={node.drain_status}")
    if (now - node.last_seen_at).total_seconds() > _node_stale_seconds():
        blockers.append("heartbeat=stale")
    if node.active_lease_count >= max(node.max_concurrency, 1):
        blockers.append("capacity=full")

    # Kind matching (use node contract if available, fallback to accepted_kinds)
    if node.accepted_kinds:
        if job.kind not in node.accepted_kinds:
            blockers.append(f"kind={job.kind}:not-in-node-contract")
    elif accepted_kinds and job.kind not in accepted_kinds:
        blockers.append("kind=not-accepted-by-runner")

    _queue_class, worker_pool = resolve_job_queue_contract_from_record(job)
    if node.worker_pools and worker_pool not in node.worker_pools:
        blockers.append(f"worker_pool!={worker_pool}")

    if job.target_os and job.target_os != node.os:
        blockers.append(f"os!={job.target_os}")
    if job.target_arch and job.target_arch != node.arch:
        blockers.append(f"arch!={job.target_arch}")
    blockers.extend(_resource_blockers(job, node))
    if job.target_zone and job.target_zone != node.zone:
        blockers.append(f"zone!={job.target_zone}")
    required_capabilities = set(job.required_capabilities or [])
    missing_capabilities = sorted(required_capabilities.difference(node.capabilities))
    if missing_capabilities:
        blockers.append(f"missing={','.join(missing_capabilities)}")

    # Edge computing constraints
    max_latency = getattr(job, "max_network_latency_ms", None)
    if max_latency and node.network_latency_ms > 0 and node.network_latency_ms > max_latency:
        blockers.append(f"latency={node.network_latency_ms}ms>{max_latency}ms")

    data_locality_key = getattr(job, "data_locality_key", None)
    prefer_cached = getattr(job, "prefer_cached_data", False)
    if data_locality_key and prefer_cached and data_locality_key not in node.cached_data_keys:
        blockers.append(f"data={data_locality_key}:not-cached")

    power_budget = getattr(job, "power_budget_watts", None)
    if power_budget and node.power_capacity_watts > 0:
        available_power = node.power_capacity_watts - node.current_power_watts
        if available_power < power_budget:
            blockers.append(f"power={available_power}W<{power_budget}W")

    thermal_sensitivity = getattr(job, "thermal_sensitivity", None)
    if thermal_sensitivity == "high" and node.thermal_state in ("hot", "throttling"):
        blockers.append(f"thermal={node.thermal_state}")

    cloud_fallback = getattr(job, "cloud_fallback_enabled", False)
    if not cloud_fallback and node.cloud_connectivity == "offline":
        blockers.append("cloud=offline:no-fallback")

    # Affinity rules (REQUIRED affinity is a blocker)
    from backend.core.scheduling_strategies import check_node_affinity

    affinity_matches, affinity_violations = check_node_affinity(job, node)
    if not affinity_matches:
        blockers.extend(affinity_violations)

    return blockers


def job_matches_node(
    job: Job,
    node: SchedulerNodeSnapshot,
    *,
    now: datetime.datetime,
    accepted_kinds: set[str] | None = None,
) -> bool:
    return not node_blockers_for_job(job, node, now=now, accepted_kinds=accepted_kinds)


def count_eligible_nodes_for_job(
    job: Job,
    active_nodes: list[SchedulerNodeSnapshot],
    *,
    now: datetime.datetime,
    accepted_kinds: set[str] | None = None,
) -> int:
    """Count eligible nodes for a job.

    Uses node contract accepted_kinds if available, otherwise falls back to
    accepted_kinds parameter (from pull request).
    """
    live_nodes = [node for node in active_nodes if is_node_eligible(node, now)]
    count = 0
    for node in _candidate_nodes_for_job(job, live_nodes, accepted_kinds=accepted_kinds):
        if job_matches_node(job, node, now=now, accepted_kinds=None):
            count += 1

    return count


def batch_eligible_counts(
    jobs: list[Job],
    active_nodes: list[SchedulerNodeSnapshot],
    *,
    now: datetime.datetime,
    accepted_kinds: set[str] | None = None,
) -> dict[str, int]:
    """Pre-compute eligible node counts for a batch of jobs.

    Shares a single live-node filter across all jobs so that
    enrollment / status / drain / heartbeat checks happen once,
    not once per (job × node) pair.
    """
    # Phase 1: filter live nodes once
    live_nodes = [n for n in active_nodes if is_node_eligible(n, now)]

    counts: dict[str, int] = {}
    for job in jobs:
        count = 0
        for node in _candidate_nodes_for_job(job, live_nodes, accepted_kinds=accepted_kinds):
            if not node_blockers_for_job(job, node, now=now, accepted_kinds=accepted_kinds):
                count += 1
        counts[job.job_id] = count
    return counts


def select_jobs_for_node(  # noqa: C901
    jobs: list[Job],
    node: SchedulerNodeSnapshot,
    active_nodes: list[SchedulerNodeSnapshot],
    *,
    now: datetime.datetime,
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str],
    active_jobs_on_node: list[Job] | None = None,
    limit: int,
    placement_plan: dict[str, str] | None = None,
) -> list[ScoredJob]:
    available_slots = max(node.max_concurrency - node.active_lease_count, 0)
    if available_slots <= 0:
        return []
    total_active_nodes = len(active_nodes)
    _active_jobs = active_jobs_on_node or []
    _reservation_mgr = None
    _backfill_eval = None
    try:
        from backend.core.backfill_scheduling import BackfillEvaluator, get_reservation_manager

        _reservation_mgr = get_reservation_manager()
        _backfill_eval = BackfillEvaluator(_reservation_mgr)
    except Exception:
        _reservation_mgr = None
        _backfill_eval = None

    # ── Phase A: Cheap pre-filter → compatible candidates ─────────────
    compatible: list[Job] = []
    for job in jobs:
        if node.accepted_kinds and job.kind not in node.accepted_kinds:
            continue
        if accepted_kinds and job.kind not in accepted_kinds:
            continue
        if job.target_os and job.target_os != node.os:
            continue
        if job.target_arch and job.target_arch != node.arch:
            continue
        # Executor contract kind-compatibility check
        from backend.core.executor_registry import get_executor_registry

        compat, _compat_reason = get_executor_registry().kind_compatible(node.executor, job.kind)
        if not compat:
            continue
        if not job_matches_node(job, node, now=now, accepted_kinds=accepted_kinds):
            continue
        if _backfill_eval is not None and _reservation_mgr is not None:
            priority = int(getattr(job, "priority", 0) or 0)
            if priority < _reservation_mgr.config.reservation_min_priority:
                can_backfill, _reason = _backfill_eval.can_backfill(job, node, now=now)
                if not can_backfill:
                    continue
        compatible.append(job)

    if not compatible:
        return []

    # ── Phase B: Batch eligible counts for pre-filtered jobs only ─────
    # Deferred to after pre-filter so we don't compute counts for
    # jobs that can never match this node.
    eligible_cache = batch_eligible_counts(
        compatible,
        active_nodes,
        now=now,
        accepted_kinds=accepted_kinds,
    )

    # ── Phase C: Score + top-K pruning ────────────────────────────────
    target_k = min(limit, available_slots)
    # Maintain a running floor: once we have K scores, any job whose
    # theoretical maximum (priority=160 + all bonuses) can't beat the
    # current K-th score is skipped.
    scored: list[ScoredJob] = []
    score_floor = -10_000  # updated once we have target_k items
    # Compute theoretical max score dynamically from active scoring weights
    from backend.core.scheduling_policy_store import get_policy_store

    _sw = get_policy_store().active.scoring
    _THEORETICAL_MAX = (
        _sw.priority_max
        + _sw.age_max
        + _sw.scarcity_max
        + _sw.reliability_max
        + _sw.strategy_max
        + _sw.zone_match_bonus
        + _sw.resource_fit_max
        + _sw.executor_match_bonus
        + _sw.data_locality_bonus
        + _sw.latency_max
        + _sw.power_max
        + _sw.thermal_max
        + _sw.affinity_max
        + _sw.sla_urgency_max
        + _sw.batch_co_location_max
    )

    for job in compatible:
        eligible_nodes_count = eligible_cache.get(job.job_id, 0)
        if eligible_nodes_count <= 0:
            continue

        # Top-K pruning: if job's theoretical best can't beat floor, skip
        if len(scored) >= target_k:
            job_priority = int(job.priority or 0)
            if job_priority + (_THEORETICAL_MAX - 160) < score_floor:
                continue

        total, breakdown = score_job_for_node(
            job,
            node,
            now=now,
            total_active_nodes=total_active_nodes,
            eligible_nodes_count=eligible_nodes_count,
            recent_failed_job_ids=recent_failed_job_ids,
            active_jobs_on_node=_active_jobs,
        )

        # ── Placement policy accept gate ─────────────────────────────
        if placement_plan and placement_plan.get(job.job_id) == node.node_id:
            plan_bonus = _get_solver_config().plan_affinity_bonus
            total += plan_bonus
            breakdown["solver_plan_affinity"] = plan_bonus

        from backend.core.placement_policy import get_placement_policy

        _pp = get_placement_policy()
        accepted, _reject_reason = _pp.accept(job, node, total)
        if not accepted:
            continue

        scored.append(
            ScoredJob(
                job=job,
                score=total,
                eligible_nodes_count=eligible_nodes_count,
                score_breakdown=breakdown,
            )
        )
        # Update floor after we have enough candidates
        if len(scored) >= target_k:
            scored.sort(key=lambda s: -s.score)
            score_floor = scored[target_k - 1].score

    scored.sort(
        key=lambda item: (
            -item.score,
            -int(item.job.priority or 0),
            item.job.created_at,
            -_stable_tiebreak(item.job.job_id, node.node_id),
        )
    )

    # ── Placement policy rerank pass ─────────────────────────────────
    from backend.core.placement_policy import get_placement_policy as _get_pp

    scored = _get_pp().rerank(scored, node)

    return scored[: min(limit, available_slots)]


# ============================================================================
# Global Placement Solver — cross-node constraint-satisfaction optimisation
# ============================================================================

# Scoring dimensions used by the solver for multi-node ranking.


def _get_solver_config() -> SolverConfig:
    from backend.core.scheduling_policy_store import get_policy_store

    return get_policy_store().active.solver


@dataclass(slots=True)
class PlacementCandidate:
    """A (job, node) pair evaluated by the solver."""

    job: Job
    node: SchedulerNodeSnapshot
    score: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)


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
        now: datetime.datetime,
        accepted_kinds: set[str],
        recent_failed_job_ids: set[str] | None = None,
        active_jobs_by_node: dict[str, list[Job]] | None = None,
        metrics: dict[str, object] | None = None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, str]:
        """Run the global placement solver.

        Returns mapping {job_id: preferred_node_id}.
        """
        if metrics is not None:
            metrics.setdefault("solver_invoked", True)
            metrics.setdefault("timed_out", False)
        if not jobs or not nodes:
            if metrics is not None:
                metrics["assignments"] = 0
                metrics["result"] = "empty_window"
            return {}

        live_nodes = [n for n in nodes if is_node_eligible(n, now)]
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
            now=now,
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
                if not job_matches_node(job, node, now=now, accepted_kinds=None):
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
            now=now,
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
                now=now,
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
        now: datetime.datetime,
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
                    min(getattr(job, "created_at", now) for job in item[1]),
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
    now: datetime.datetime,
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str] | None = None,
    active_jobs_by_node: dict[str, list[Job]] | None = None,
    decision_context: dict[str, object] | None = None,
) -> dict[str, str]:
    """Run the global solver only when it fits a strict dispatch latency budget."""
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
        now=now,
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
