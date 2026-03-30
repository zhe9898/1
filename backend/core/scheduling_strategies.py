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

    Returns: 0-100 (higher = better for binpack)
    """
    if node.max_concurrency <= 0:
        return 0

    # Prefer nodes with higher utilization (but not full)
    utilization = node.active_lease_count / node.max_concurrency
    if utilization >= 1.0:
        return 0  # Full nodes get 0 score

    # Favor nodes that are already warm (50-90% utilized)
    if 0.5 <= utilization <= 0.9:
        return 100
    elif utilization < 0.5:
        return int(100 * utilization * 2)  # 0-50% util -> 0-100 score
    else:
        return int(100 * (1.0 - (utilization - 0.9) * 10))  # 90-100% util -> 100-0 score


def calculate_locality_score(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Calculate locality score (data + network proximity).

    Returns: 0-100 (higher = better locality)
    """
    score = 0

    # Data locality (50 points max)
    data_locality_key = getattr(job, "data_locality_key", None)
    if data_locality_key:
        if data_locality_key in node.cached_data_keys:
            score += 50
        elif len(node.cached_data_keys) > 0:
            # Partial credit if node has other cached data (might have related data)
            score += 10

    # Network proximity (50 points max)
    max_latency = getattr(job, "max_network_latency_ms", None)
    if max_latency and node.network_latency_ms > 0:
        latency_ratio = 1.0 - min(node.network_latency_ms / max_latency, 1.0)
        score += int(50 * latency_ratio)
    elif node.network_latency_ms == 0:
        # Local node (no network latency)
        score += 50

    return min(score, 100)


def calculate_performance_score(node: SchedulerNodeSnapshot) -> int:
    """Calculate performance score (prefer faster/more capable nodes).

    Returns: 0-100 (higher = better performance)
    """
    score = 0

    # Reliability (40 points max)
    score += int(40 * node.reliability_score)

    # Resource capacity (30 points max)
    # Normalize by typical values: 8 cores, 16GB RAM, 8GB VRAM
    cpu_score = min(node.cpu_cores / 8.0, 1.0) * 10
    memory_score = min(node.memory_mb / 16384.0, 1.0) * 10
    gpu_score = min(node.gpu_vram_mb / 8192.0, 1.0) * 10
    score += int(cpu_score + memory_score + gpu_score)

    # Thermal state (15 points max)
    if node.thermal_state == "cool":
        score += 15
    elif node.thermal_state == "normal":
        score += 10
    elif node.thermal_state == "warm":
        score += 5

    # Bandwidth (15 points max)
    if node.bandwidth_mbps > 0:
        # Normalize by 1Gbps = 1000 Mbps
        score += int(15 * min(node.bandwidth_mbps / 1000.0, 1.0))

    return min(score, 100)


def calculate_balanced_score(job: Job, node: SchedulerNodeSnapshot) -> int:
    """Calculate balanced score (mix of spread, locality, and performance).

    Returns: 0-100 (higher = better balance)
    """
    spread = calculate_spread_score(node)
    locality = calculate_locality_score(job, node)
    performance = calculate_performance_score(node)

    # Weighted average: 40% spread, 30% locality, 30% performance
    return int(0.4 * spread + 0.3 * locality + 0.3 * performance)


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
            return 50  # Heavy penalty for anti-affinity violation

    return 0
