from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass

from backend.models.job import Job
from backend.models.node import Node

_NODE_STALE_AFTER_SECONDS = 45


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
    return (now - node.last_seen_at).total_seconds() <= _NODE_STALE_AFTER_SECONDS


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


def _resource_fit_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    bonus = 0
    if (getattr(job, "target_executor", None) or "").strip() == node.executor:
        bonus += 12
    for required, available in (
        (max(int(getattr(job, "required_cpu_cores", 0) or 0), 0), node.cpu_cores),
        (max(int(getattr(job, "required_memory_mb", 0) or 0), 0), node.memory_mb),
        (max(int(getattr(job, "required_gpu_vram_mb", 0) or 0), 0), node.gpu_vram_mb),
        (max(int(getattr(job, "required_storage_mb", 0) or 0), 0), node.storage_mb),
    ):
        if required <= 0 or available <= 0:
            continue
        closeness = min(required / max(available, 1), 1.0)
        bonus += int(6 * closeness)
    return bonus


def node_blockers_for_job(
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
    if (now - node.last_seen_at).total_seconds() > _NODE_STALE_AFTER_SECONDS:
        blockers.append("heartbeat=stale")
    if node.active_lease_count >= max(node.max_concurrency, 1):
        blockers.append("capacity=full")

    # Kind matching (use node contract if available, fallback to accepted_kinds)
    if node.accepted_kinds:
        if job.kind not in node.accepted_kinds:
            blockers.append(f"kind={job.kind}:not-in-node-contract")
    elif accepted_kinds and job.kind not in accepted_kinds:
        blockers.append("kind=not-accepted-by-runner")

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
    count = 0
    for node in active_nodes:
        # Use node contract accepted_kinds if available
        if node.accepted_kinds:
            if job.kind not in node.accepted_kinds:
                continue
        elif accepted_kinds and job.kind not in accepted_kinds:
            continue

        if job_matches_node(job, node, now=now, accepted_kinds=None):
            count += 1

    return count


def _stable_tiebreak(job_id: str, node_id: str) -> int:
    digest = hashlib.sha1(f"{job_id}:{node_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _power_efficiency_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Power headroom bonus (0-15): prefer nodes with more available power."""
    power_budget = getattr(job, "power_budget_watts", None)
    if not power_budget or node.power_capacity_watts <= 0:
        return 0
    available_power = node.power_capacity_watts - node.current_power_watts
    if available_power < power_budget:
        return 0
    headroom_ratio = min((available_power - power_budget) / power_budget, 1.0)
    return int(15 * headroom_ratio)


def _thermal_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Thermal state bonus (0-10): prefer cooler nodes for thermal-sensitive jobs."""
    thermal_sensitivity = getattr(job, "thermal_sensitiv ity", None)
    if thermal_sensitivity != "high":
        return 0
    if node.thermal_state == "cool":
        return 10
    if node.thermal_state == "normal":
        return 5
    return 0


def _affinity_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Affinity match bonus (0-20)."""
    from backend.core.scheduling_strategies import check_node_affinity

    affinity_matches, _ = check_node_affinity(job, node)
    if not affinity_matches:
        return 0
    affinity_labels = getattr(job, "affinity_labels", None) or {}
    return 20 if affinity_labels else 0


def _sla_risk_to_score(risk: float, level: str) -> int:
    """Convert SLA breach risk into a scoring bonus (0-30).

    Higher risk → higher urgency → higher score to prioritize the job.
    """
    if level in ("critical", "breached"):
        return 30
    if level == "high":
        return 20
    if level == "medium":
        return 10
    return 0


def _batch_co_location_bonus(job: Job, active_jobs_on_node: list[Job]) -> int:
    """Bonus for scheduling a job on a node already running same-batch jobs (0-15)."""
    batch_key = getattr(job, "batch_key", None)
    if not batch_key:
        return 0
    co_located = sum(1 for j in active_jobs_on_node if getattr(j, "batch_key", None) == batch_key)
    return min(co_located * 5, 15)


def score_job_for_node(
    job: Job,
    node: SchedulerNodeSnapshot,
    *,
    now: datetime.datetime,
    total_active_nodes: int,
    eligible_nodes_count: int,
    recent_failed_job_ids: set[str],
    active_jobs_on_node: list[Job] | None = None,
) -> int:
    """Score job-node match with edge computing factors and scheduling strategies.

    Returns (total_score, breakdown_dict) for explain-trace debugging.

    Scoring components:
    - Priority: 0-100 (base job priority)
    - Age: 0-60 (minutes waiting, capped at 1 hour)
    - Scarcity: 0-100 (fewer eligible nodes = higher score)
    - Reliability: 0-20 (node success rate)
    - Strategy: 0-100 (scheduling strategy bonus, includes locality/spread/etc.)
    - Zone: 0-10 (zone match — data/latency locality is in strategy layer)
    - Resource fit: 0-24 (executor match + resource closeness)
    - Power efficiency: 0-15 (available power headroom)
    - Thermal: 0-10 (thermal state bonus)
    - Affinity: 0-20 (affinity match bonus)
    - SLA urgency: 0-30 (approaching SLA breach)
    - Batch bonus: 0-15 (batch scheduling co-location)
    - Load penalty: 0-40 (current node load)
    - Recent failure penalty: 0-40 (job failed on this node recently)
    - Anti-affinity penalty: 0-50 (anti-affinity violation)

    Total range: -130 to 504
    """
    from backend.core.scheduling_strategies import (
        SchedulingStrategy,
        calculate_anti_affinity_penalty,
        calculate_strategy_score,
    )
    from backend.core.business_scheduling import calculate_sla_breach_risk

    # ── Positive dimensions ──────────────────────────────────────────
    priority_score = max(0, min(int(job.priority or 0), 100))

    age_minutes = max(int((now - job.created_at).total_seconds() // 60), 0)
    age_score = min(age_minutes, 60)

    scarcity_score = (
        int(100 * max(total_active_nodes - eligible_nodes_count, 0) / total_active_nodes)
        if total_active_nodes > 0
        else 0
    )

    reliability_score = int(max(0.0, min(node.reliability_score, 1.0)) * 20)

    strategy = getattr(job, "scheduling_strategy", None) or SchedulingStrategy.SPREAD
    try:
        strategy_enum = SchedulingStrategy(strategy)
    except ValueError:
        strategy_enum = SchedulingStrategy.SPREAD
    strategy_score = calculate_strategy_score(strategy_enum, job, node)

    zone_bonus = 10 if (job.target_zone and job.target_zone == node.zone) else 0

    sla_risk, sla_level = calculate_sla_breach_risk(job, now=now)

    _active_jobs = active_jobs_on_node or []
    breakdown = {
        "priority": priority_score,
        "age": age_score,
        "scarcity": scarcity_score,
        "reliability": reliability_score,
        "strategy": strategy_score,
        "zone": zone_bonus,
        "resource_fit": _resource_fit_bonus(job, node),
        "power": _power_efficiency_bonus(job, node),
        "thermal": _thermal_bonus(job, node),
        "affinity": _affinity_bonus(job, node),
        "sla_urgency": _sla_risk_to_score(sla_risk, sla_level),
        "batch": _batch_co_location_bonus(job, _active_jobs),
    }

    # ── Negative dimensions (penalties) ──────────────────────────────
    load_penalty = int(40 * (node.active_lease_count / max(node.max_concurrency, 1)))
    recent_failure_penalty = 40 if job.job_id in recent_failed_job_ids else 0
    anti_affinity_penalty = calculate_anti_affinity_penalty(
        job, node, active_jobs_on_node=_active_jobs,
    )
    breakdown["load_penalty"] = -load_penalty
    breakdown["failure_penalty"] = -recent_failure_penalty
    breakdown["anti_affinity_penalty"] = -anti_affinity_penalty

    total = sum(breakdown.values())
    return total, breakdown


def select_jobs_for_node(
    jobs: list[Job],
    node: SchedulerNodeSnapshot,
    active_nodes: list[SchedulerNodeSnapshot],
    *,
    now: datetime.datetime,
    accepted_kinds: set[str],
    recent_failed_job_ids: set[str],
    active_jobs_on_node: list[Job] | None = None,
    limit: int,
) -> list[ScoredJob]:
    available_slots = max(node.max_concurrency - node.active_lease_count, 0)
    if available_slots <= 0:
        return []
    total_active_nodes = len(active_nodes)
    _active_jobs = active_jobs_on_node or []
    scored: list[ScoredJob] = []
    for job in jobs:
        if not job_matches_node(job, node, now=now, accepted_kinds=accepted_kinds):
            continue
        eligible_nodes_count = count_eligible_nodes_for_job(job, active_nodes, now=now, accepted_kinds=accepted_kinds)
        if eligible_nodes_count <= 0:
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
        scored.append(
            ScoredJob(
                job=job,
                score=total,
                eligible_nodes_count=eligible_nodes_count,
                score_breakdown=breakdown,
            )
        )

    scored.sort(
        key=lambda item: (
            -item.score,
            -int(item.job.priority or 0),
            item.job.created_at,
            -_stable_tiebreak(item.job.job_id, node.node_id),
        )
    )
    return scored[: min(limit, available_slots)]
