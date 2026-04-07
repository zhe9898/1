from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from backend.core.job_scoring import (  # noqa: F401 鈥?re-export
    _stable_tiebreak,
    score_job_for_node,
)
from backend.core.placement_solver import (  # noqa: F401 鈥?re-export
    PlacementCandidate,
    PlacementSolver,
    _get_solver_config,
    build_time_budgeted_placement_plan,
    get_placement_solver,
)
from backend.core.scheduling_candidates import (  # noqa: F401 鈥?re-export
    _bool_attr,
    _candidate_nodes_for_job,
    _has_items_attr,
    _int_attr,
    _job_attr,
    _required_capability_set,
    _text_attr,
    batch_eligible_counts,
    count_eligible_nodes_for_job,
)
from backend.core.worker_pool import infer_node_worker_pools, resolve_job_queue_contract_from_record
from backend.models.job import Job
from backend.models.node import Node

logger = logging.getLogger(__name__)

__all__ = [
    # Core types
    "SchedulerNodeSnapshot",
    "ScoredJob",
    # Node operations
    "build_node_snapshot",
    "is_node_eligible",
    "node_blockers_for_job",
    "job_matches_node",
    "select_jobs_for_node",
    # Re-exports from job_scoring
    "score_job_for_node",
    # Re-exports from placement_solver
    "PlacementCandidate",
    "PlacementSolver",
    "build_time_budgeted_placement_plan",
    "get_placement_solver",
    # Re-exports from scheduling_candidates
    "batch_eligible_counts",
    "count_eligible_nodes_for_job",
]


def _node_stale_seconds() -> int:
    from backend.core.scheduling_policy_store import get_policy_store

    return get_policy_store().active.freshness.stale_after_seconds


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
    if node.enrollment_status != "approved":
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
    if node.enrollment_status != "approved":
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
    from backend.core.backfill_scheduling import BackfillEvaluator, get_reservation_manager

    backfill_gate = BackfillEvaluator(get_reservation_manager())

    # ── Phase A: Cheap pre-filter → compatible candidates ─────────────────
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
        can_backfill, _reason = backfill_gate.can_backfill(job, node, now=now)
        if not can_backfill:
            continue
        compatible.append(job)

    if not compatible:
        return []

    # ── Phase B: Batch eligible counts for pre-filtered jobs only ─────────
    # Deferred to after pre-filter so we don't compute counts for
    # jobs that can never match this node.
    eligible_cache = batch_eligible_counts(
        compatible,
        active_nodes,
        now=now,
        accepted_kinds=accepted_kinds,
    )

    # 鈹€鈹€ Phase C: Score + top-K pruning 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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

        # 鈹€鈹€ Placement policy accept gate 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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

    # 鈹€鈹€ Placement policy rerank pass 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    from backend.core.placement_policy import get_placement_policy as _get_pp

    scored = _get_pp().rerank(scored, node)

    return scored[: min(limit, available_slots)]
