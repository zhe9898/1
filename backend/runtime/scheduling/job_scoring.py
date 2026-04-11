"""Job-to-node scoring helpers.

Extracted from job_scheduler.py for maintainability.
Contains all scoring bonus/penalty helpers and the main
``score_job_for_node`` function.
"""

from __future__ import annotations

import datetime
import hashlib
import math
from typing import TYPE_CHECKING

from backend.kernel.policy.types import NodeFreshnessPolicy, ScoringWeights
from backend.runtime.scheduling.business_scheduling import calculate_sla_breach_risk
from backend.runtime.scheduling.scheduling_strategies import (
    SchedulingStrategy,
    calculate_anti_affinity_penalty,
    calculate_strategy_score,
    check_node_affinity,
)

if TYPE_CHECKING:
    from backend.models.job import Job
    from backend.runtime.scheduling.job_scheduler import SchedulerNodeSnapshot


def _get_scoring_weights() -> ScoringWeights:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.scoring


def _get_freshness_policy() -> NodeFreshnessPolicy:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.freshness


def _resource_fit_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    sw = _get_scoring_weights()
    bonus = 0
    if (getattr(job, "target_executor", None) or "").strip() == node.executor:
        bonus += sw.executor_match_bonus
    for required, available in (
        (max(int(getattr(job, "required_cpu_cores", 0) or 0), 0), node.cpu_cores),
        (max(int(getattr(job, "required_memory_mb", 0) or 0), 0), node.memory_mb),
        (max(int(getattr(job, "required_gpu_vram_mb", 0) or 0), 0), node.gpu_vram_mb),
        (max(int(getattr(job, "required_storage_mb", 0) or 0), 0), node.storage_mb),
    ):
        if required <= 0 or available <= 0:
            continue
        closeness = min(required / max(available, 1), 1.0)
        bonus += int(sw.resource_closeness_per_dim * closeness)
    return bonus


def _stable_tiebreak(job_id: str, node_id: str) -> int:
    digest = hashlib.sha1(f"{job_id}:{node_id}".encode("utf-8")).hexdigest()  # nosec  # noqa: S324
    return int(digest[:8], 16)


def _freshness_penalty(node: SchedulerNodeSnapshot, now: datetime.datetime) -> int:
    """Heartbeat freshness penalty: prefer nodes with recent heartbeats."""
    fp = _get_freshness_policy()
    sw = _get_scoring_weights()
    age_seconds = (now - node.last_seen_at).total_seconds()
    if age_seconds <= fp.grace_period_seconds:
        return 0
    ratio = min(
        (age_seconds - fp.grace_period_seconds) / max(fp.stale_after_seconds - fp.grace_period_seconds, 1),
        1.0,
    )
    return int(sw.freshness_penalty_max * ratio)


def _power_efficiency_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Power headroom bonus: prefer nodes with more available power."""
    power_budget = getattr(job, "power_budget_watts", None)
    if not power_budget or node.power_capacity_watts <= 0:
        return 0
    available_power = node.power_capacity_watts - node.current_power_watts
    if available_power < power_budget:
        return 0
    headroom_ratio = min((available_power - power_budget) / power_budget, 1.0)
    sw = _get_scoring_weights()
    return int(sw.power_max * headroom_ratio)


def _thermal_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Thermal state bonus: prefer cooler nodes for thermal-sensitive jobs."""
    thermal_sensitivity = getattr(job, "thermal_sensitivity", None)
    if thermal_sensitivity != "high":
        return 0
    sw = _get_scoring_weights()
    if node.thermal_state == "cool":
        return sw.thermal_max
    if node.thermal_state == "normal":
        return sw.thermal_max // 2
    return 0


def _affinity_bonus(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Affinity match bonus."""
    affinity_matches, _ = check_node_affinity(job, node)
    if not affinity_matches:
        return 0
    affinity_labels = getattr(job, "affinity_labels", None) or {}
    sw = _get_scoring_weights()
    return sw.affinity_max if affinity_labels else 0


def _sla_risk_to_score(risk: float, level: str) -> int:
    """Convert SLA breach risk into a scoring bonus."""
    sw = _get_scoring_weights()
    if level in ("critical", "breached"):
        return sw.sla_urgency_max
    if level == "high":
        return int(sw.sla_urgency_max * 2 / 3)
    if level == "medium":
        return int(sw.sla_urgency_max / 3)
    return 0


def _device_profile_bonus(job: "Job", node: "SchedulerNodeSnapshot") -> int:
    """Soft bonus when the node's device profile matches the job's preference.

    This is *never* a hard filter 鈥?jobs still run on non-matching nodes.
    """
    preferred = getattr(job, "preferred_device_profile", None)
    if not preferred:
        return 0
    metadata = node.metadata_json if isinstance(node.metadata_json, dict) else {}
    node_profile = metadata.get("device_profile") if metadata else None
    if not node_profile:
        return 0
    sw = _get_scoring_weights()
    return sw.device_profile_bonus if str(node_profile) == str(preferred) else 0


def _batch_co_location_bonus(job: Job, active_jobs_on_node: list[Job]) -> int:
    """Bonus for scheduling a job on a node already running same-batch jobs."""
    batch_key = getattr(job, "batch_key", None)
    if not batch_key:
        return 0
    sw = _get_scoring_weights()
    co_located = sum(1 for j in active_jobs_on_node if getattr(j, "batch_key", None) == batch_key)
    return min(co_located * sw.batch_per_co_located, sw.batch_co_location_max)


def score_job_for_node(
    job: Job,
    node: SchedulerNodeSnapshot,
    *,
    now: datetime.datetime,
    total_active_nodes: int,
    eligible_nodes_count: int,
    recent_failed_job_ids: set[str],
    active_jobs_on_node: list[Job] | None = None,
) -> tuple[int, dict[str, int]]:
    """Score job-node match with edge computing factors and scheduling strategies.

    Returns (total_score, breakdown_dict) for explain-trace debugging.

    Scoring components:
    - Priority: 0-160 (base priority, extended for business boost)
    - Age: 0-60 (logarithmic curve, 30-min half-life)
    - Scarcity: 0-100 (fewer eligible nodes = higher score)
    - Reliability: 0-20 (node success rate)
    - Strategy: 0-100 (scheduling strategy bonus, includes locality/spread/etc.)
    - Zone: 0-10 (zone match)
    - Resource fit: 0-24 (executor match + resource closeness)
    - Power efficiency: 0-15 (available power headroom)
    - Thermal: 0-10 (thermal state bonus)
    - Affinity: 0-20 (affinity match bonus)
    - SLA urgency: 0-30 (approaching SLA breach)
    - Batch bonus: 0-15 (batch scheduling co-location)
    - Load penalty: 0-40 (current node load)
    - Freshness penalty: 0-15 (heartbeat freshness decay)
    - Recent failure penalty: 0-40 (job failed on this node recently)
    - Anti-affinity penalty: 0-50 (anti-affinity violation)

    Total range: -145 to 564
    """
    # 鈹€鈹€ Positive dimensions 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    sw = _get_scoring_weights()

    priority_score = max(0, min(int(job.priority or 0), sw.priority_max))

    age_seconds = max((now - job.created_at).total_seconds(), 0)
    age_score = int(sw.age_max * (1.0 - math.exp(-age_seconds / sw.age_half_life_seconds)))

    scarcity_score = int(sw.scarcity_max * max(total_active_nodes - eligible_nodes_count, 0) / total_active_nodes) if total_active_nodes > 0 else 0

    reliability_score = int(max(0.0, min(node.reliability_score, 1.0)) * sw.reliability_max)

    from backend.runtime.scheduling.scheduler_auto_tune import get_scheduler_tuner

    _tuner = get_scheduler_tuner()

    strategy = getattr(job, "scheduling_strategy", None)
    if not strategy:
        strategy = _tuner.recommend_strategy() or SchedulingStrategy.SPREAD
    try:
        strategy_enum = SchedulingStrategy(strategy)
    except ValueError:
        strategy_enum = SchedulingStrategy.SPREAD
    strategy_score = calculate_strategy_score(strategy_enum, job, node)

    zone_bonus = sw.zone_match_bonus if (job.target_zone and job.target_zone == node.zone) else 0

    # 鈹€鈹€ Edge computing scoring factors 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    data_locality_key = getattr(job, "data_locality_key", None)
    data_locality_bonus = 0
    if data_locality_key and data_locality_key in getattr(node, "cached_data_keys", set()):
        data_locality_bonus = sw.data_locality_bonus

    network_latency_ms = getattr(node, "network_latency_ms", 0) or 0
    latency_bonus = max(0, sw.latency_max - int(network_latency_ms / 10)) if network_latency_ms > 0 else sw.latency_default

    power_bonus = _power_efficiency_bonus(job, node)
    thermal_bonus = _thermal_bonus(job, node)

    sla_risk, sla_level = calculate_sla_breach_risk(job, now=now)

    _active_jobs = active_jobs_on_node or []

    # 鈹€鈹€ Self-learning weight adjustments 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    _adj = _tuner.get_adjustment  # shorthand

    breakdown = {
        "priority": int(priority_score * _adj("priority")),
        "age": int(age_score * _adj("age")),
        "scarcity": int(scarcity_score * _adj("scarcity")),
        "reliability": int(reliability_score * _adj("reliability")),
        "strategy": int(strategy_score * _adj("strategy")),
        "zone": int(zone_bonus * _adj("zone")),
        "resource_fit": int(_resource_fit_bonus(job, node) * _adj("resource_fit")),
        "data_locality": int(data_locality_bonus * _adj("data_locality")),
        "latency": int(latency_bonus * _adj("latency")),
        "power": int(power_bonus * _adj("power")),
        "thermal": int(thermal_bonus * _adj("thermal")),
        "device_profile": int(_device_profile_bonus(job, node) * _adj("device_profile")),
        "affinity": int(_affinity_bonus(job, node) * _adj("affinity")),
        "sla_urgency": int(_sla_risk_to_score(sla_risk, sla_level) * _adj("sla_urgency")),
        "batch": int(_batch_co_location_bonus(job, _active_jobs) * _adj("batch")),
    }

    # 鈹€鈹€ Negative dimensions (penalties, also tunable) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    load_penalty = int(sw.load_penalty_max * (node.active_lease_count / max(node.max_concurrency, 1)))
    recent_failure_penalty = sw.failure_penalty if job.job_id in recent_failed_job_ids else 0
    anti_affinity_penalty = calculate_anti_affinity_penalty(
        job,
        node,
        active_jobs_on_node=_active_jobs,
    )
    freshness = _freshness_penalty(node, now)
    breakdown["load_penalty"] = -int(load_penalty * _adj("load_penalty"))
    breakdown["freshness_penalty"] = -int(freshness * _adj("freshness_penalty"))
    breakdown["failure_penalty"] = -int(recent_failure_penalty * _adj("failure_penalty"))
    breakdown["anti_affinity_penalty"] = -anti_affinity_penalty

    # 鈹€鈹€ Learned node performance bias 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    _node_bias = _tuner.get_node_bias(node.node_id)
    if _node_bias != 0.0:
        breakdown["learned_node_bias"] = int(_node_bias)

    total = sum(breakdown.values())

    # 鈹€鈹€ System-level placement policy adjustments 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    from backend.runtime.scheduling.placement_policy import get_placement_policy

    _pp = get_placement_policy()
    total, breakdown = _pp.adjust_score(job, node, total, breakdown)

    return total, breakdown
