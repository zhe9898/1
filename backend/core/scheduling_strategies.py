"""
Advanced Scheduling Strategies for Edge Computing

Provides multiple scheduling strategies optimized for different workload patterns:
- Spread: Distribute load evenly across nodes (default)
- Binpack: Pack jobs tightly to minimize active nodes (power efficiency)
- Locality: Prioritize data locality and network proximity
- Performance: Prioritize fastest/most capable nodes
- Balanced: Balance between spread and performance
"""

from __future__ import annotations

import math
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.job_scheduler import SchedulerNodeSnapshot
    from backend.models.job import Job


class SchedulingStrategy(str, Enum):
    """Scheduling strategy for job placement."""

    SPREAD = "spread"  # Distribute load evenly (default)
    BINPACK = "binpack"  # Pack tightly to minimize active nodes
    LOCALITY = "locality"  # Prioritize data locality and network proximity
    PERFORMANCE = "performance"  # Prioritize fastest/most capable nodes
    BALANCED = "balanced"  # Balance between spread and performance


class NodeAffinityRule(str, Enum):
    """Node affinity rule for job placement."""

    REQUIRED = "required"  # Job MUST run on nodes matching affinity
    PREFERRED = "preferred"  # Job SHOULD run on nodes matching affinity (soft constraint)


def _get_strategy_config():
    from backend.core.scheduling_policy_store import get_policy_store
    return get_policy_store().active.strategy


def calculate_spread_score(node: SchedulerNodeSnapshot) -> int:
    """Calculate spread score (prefer nodes with lower load).

    Returns: 0-100 (higher = better for spread)
    """
    if node.max_concurrency <= 0:
        return 0

    # Prefer nodes with lower utilization
    utilization = node.active_lease_count / node.max_concurrency
    return int(100 * (1.0 - utilization))


def calculate_binpack_score(node: SchedulerNodeSnapshot) -> int:
    """Calculate binpack score (prefer nodes with higher load).

    Uses a Gaussian curve peaking at 75% utilization for smooth scoring.
    Avoids the step-function discontinuity of plateau+cliff approaches.

    Returns: 0-100 (higher = better for binpack)
    """
    if node.max_concurrency <= 0:
        return 0

    utilization = node.active_lease_count / node.max_concurrency
    if utilization >= 1.0:
        return 0  # Full nodes get 0 score

    bp = _get_strategy_config().binpack
    score = math.exp(-((utilization - bp.peak_utilization) ** 2) / (2 * bp.sigma * bp.sigma)) * 100
    return int(max(0, min(100, score)))


def calculate_locality_score(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Calculate locality score (data + network proximity + throughput).

    Returns: 0-100 (higher = better locality)
    """
    lc = _get_strategy_config().locality
    score = 0

    data_locality_key = getattr(job, "data_locality_key", None)
    if data_locality_key:
        if data_locality_key in node.cached_data_keys:
            score += lc.data_locality_points
        elif len(node.cached_data_keys) > 0:
            score += lc.partial_cache_points

    max_latency = getattr(job, "max_network_latency_ms", None)
    if max_latency and node.network_latency_ms > 0:
        latency_ratio = 1.0 - min(node.network_latency_ms / max_latency, 1.0)
        score += int(lc.network_proximity_points * latency_ratio)
    elif node.network_latency_ms == 0:
        score += lc.network_proximity_points

    bw_sat = max(lc.bandwidth_saturation_mbps, 1.0)
    if data_locality_key and node.bandwidth_mbps > 0:
        bw_ratio = min(node.bandwidth_mbps / bw_sat, 1.0)
        score += int(lc.bandwidth_points * bw_ratio)
    elif node.bandwidth_mbps > 0:
        score += int(lc.non_local_bandwidth_points * min(node.bandwidth_mbps / bw_sat, 1.0))

    return min(score, 100)


def calculate_performance_score(
    node: SchedulerNodeSnapshot,
) -> int:
    """Calculate performance score (prefer faster/more capable nodes).

    Returns: 0-100 (higher = better performance)
    """
    pc = _get_strategy_config().performance
    score = 0.0

    score += pc.reliability_weight * node.reliability_score

    cpu_score = min(node.cpu_cores / max(pc.ref_cpu, 1), 1.0) * pc.cpu_weight
    memory_score = min(node.memory_mb / max(pc.ref_memory_mb, 1), 1.0) * pc.memory_weight
    gpu_score = min(node.gpu_vram_mb / max(pc.ref_gpu_vram_mb, 1), 1.0) * pc.gpu_weight
    storage_score = min(node.storage_mb / max(pc.ref_memory_mb, 1), 1.0) * pc.storage_weight
    score += cpu_score + memory_score + gpu_score + storage_score

    if node.thermal_state == "cool":
        score += pc.thermal_cool
    elif node.thermal_state == "normal":
        score += pc.thermal_normal
    elif node.thermal_state == "warm":
        score += pc.thermal_warm

    if node.bandwidth_mbps > 0:
        score += pc.bandwidth_weight * min(node.bandwidth_mbps / max(pc.ref_bandwidth_mbps, 1), 1.0)

    if node.power_capacity_watts > 0:
        headroom = (node.power_capacity_watts - node.current_power_watts) / node.power_capacity_watts
        score += pc.power_headroom_weight * max(0.0, headroom)

    return min(int(score), 100)


def calculate_balanced_score(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Calculate balanced score with adaptive weights.

    Weights adapt to job characteristics:
    - Jobs with data_locality_key: heavier locality weight
    - Jobs with required_gpu_vram_mb: heavier performance weight
    - Default: spread-oriented for even distribution

    Returns: 0-100 (higher = better balance)
    """
    spread = calculate_spread_score(node)
    locality = calculate_locality_score(job, node)
    performance = calculate_performance_score(node)

    bw = _get_strategy_config().balanced
    has_locality_need = bool(getattr(job, "data_locality_key", None))
    has_heavy_compute = bool(getattr(job, "required_gpu_vram_mb", 0))

    if has_locality_need and has_heavy_compute:
        w = bw.locality_gpu
    elif has_locality_need:
        w = bw.locality_only
    elif has_heavy_compute:
        w = bw.compute_heavy
    else:
        w = bw.default

    return int(w[0] * spread + w[1] * locality + w[2] * performance)


def calculate_strategy_score(
    strategy: SchedulingStrategy,
    job: Job,
    node: SchedulerNodeSnapshot,
) -> int:
    """Calculate score based on scheduling strategy.

    Returns: 0-100 (higher = better match for strategy)
    """
    if strategy == SchedulingStrategy.SPREAD:
        return calculate_spread_score(node)
    elif strategy == SchedulingStrategy.BINPACK:
        return calculate_binpack_score(node)
    elif strategy == SchedulingStrategy.LOCALITY:
        return calculate_locality_score(job, node)
    elif strategy == SchedulingStrategy.PERFORMANCE:
        return calculate_performance_score(node)
    elif strategy == SchedulingStrategy.BALANCED:
        return calculate_balanced_score(job, node)
    else:
        # Default to spread
        return calculate_spread_score(node)


def check_node_affinity(
    job: Job,
    node: SchedulerNodeSnapshot,
) -> tuple[bool, list[str]]:
    """Check if node matches job's affinity rules.

    Returns: (matches, reasons)
    - matches: True if node satisfies affinity rules
    - reasons: List of affinity violations (empty if matches)
    """
    affinity_labels = getattr(job, "affinity_labels", None) or {}
    affinity_rule = getattr(job, "affinity_rule", None)

    if not affinity_labels:
        return True, []

    # Check if node metadata matches affinity labels
    node_metadata = node.metadata_json if hasattr(node, "metadata_json") else {}
    violations = []

    for key, value in affinity_labels.items():
        node_value = node_metadata.get(key)
        if node_value != value:
            violations.append(f"affinity:{key}={value}:node-has={node_value}")

    if violations:
        if affinity_rule == NodeAffinityRule.REQUIRED:
            return False, violations
        else:
            # PREFERRED affinity - not a blocker, just a preference
            return True, violations

    return True, []


def calculate_anti_affinity_penalty(
    job: Job,
    node: SchedulerNodeSnapshot,
    active_jobs_on_node: list[Job],
) -> int:
    """Calculate penalty for anti-affinity violations.

    Anti-affinity prevents jobs with same anti_affinity_key from running on same node.

    Returns: 0-50 (penalty points to subtract from score)
    """
    anti_affinity_key = getattr(job, "anti_affinity_key", None)
    if not anti_affinity_key:
        return 0

    # Check if any active job on this node has the same anti_affinity_key
    for active_job in active_jobs_on_node:
        active_key = getattr(active_job, "anti_affinity_key", None)
        if active_key == anti_affinity_key:
            return _get_strategy_config().anti_affinity_penalty

    return 0
