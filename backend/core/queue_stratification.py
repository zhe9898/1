"""
Queue Stratification Module

Implements priority-based queue stratification for fair and predictable job scheduling.
"""

from __future__ import annotations

from typing import Final

# ============================================================================
# Priority Layer Definitions
# ============================================================================

PRIORITY_LAYERS: Final[dict[str, tuple[int, int]]] = {
    "critical": (90, 100),
    "high": (70, 89),
    "normal": (40, 69),
    "low": (20, 39),
    "batch": (0, 19),
}

PRIORITY_LAYER_ORDER: Final[list[str]] = [
    "critical",
    "high",
    "normal",
    "low",
    "batch",
]


def get_priority_layer(priority: int) -> str:
    """Get priority layer name for a given priority value.

    Args:
        priority: Priority value (0-100)

    Returns:
        Priority layer name (critical, high, normal, low, batch)

    Examples:
        >>> get_priority_layer(95)
        'critical'
        >>> get_priority_layer(50)
        'normal'
        >>> get_priority_layer(10)
        'batch'
    """
    priority = max(0, min(100, priority))  # Clamp to 0-100

    for layer_name, (min_priority, max_priority) in PRIORITY_LAYERS.items():
        if min_priority <= priority <= max_priority:
            return layer_name

    # Fallback (should never happen due to clamping)
    return "normal"


def calculate_effective_priority(
    base_priority: int,
    wait_time_seconds: float,
    *,
    aging_enabled: bool = True,
    aging_interval_seconds: int = 300,
    aging_bonus_per_interval: int = 1,
    aging_max_bonus: int = 20,
) -> int:
    """Calculate effective priority with aging bonus.

    Args:
        base_priority: Base priority value (0-100)
        wait_time_seconds: Time job has been waiting in queue
        aging_enabled: Whether aging is enabled
        aging_interval_seconds: Seconds per aging interval (default 300 = 5 min)
        aging_bonus_per_interval: Priority bonus per interval (default 1)
        aging_max_bonus: Maximum aging bonus (default 20)

    Returns:
        Effective priority (0-100)

    Examples:
        >>> calculate_effective_priority(30, 3600)  # Low priority, 1 hour wait
        42  # 30 + 12 (12 intervals of 5 min)

        >>> calculate_effective_priority(30, 7200)  # Low priority, 2 hours wait
        50  # 30 + 20 (max bonus)

        >>> calculate_effective_priority(90, 3600)  # Critical priority
        100  # 90 + 10, clamped to 100
    """
    if not aging_enabled or wait_time_seconds <= 0:
        return base_priority

    # Calculate aging bonus
    intervals = int(wait_time_seconds // aging_interval_seconds)
    aging_bonus = min(intervals * aging_bonus_per_interval, aging_max_bonus)

    # Apply bonus and clamp to 0-100
    effective_priority = base_priority + aging_bonus
    return max(0, min(100, effective_priority))


def get_priority_layer_stats(jobs: list[object]) -> dict[str, dict[str, object]]:
    """Get statistics about jobs grouped by priority layer.

    Args:
        jobs: List of job objects with 'priority' and 'created_at' attributes

    Returns:
        Dictionary mapping layer name to stats:
        {
            "critical": {"count": 5, "oldest": datetime},
            "high": {"count": 20, "oldest": datetime},
            ...
        }
    """

    stats: dict[str, dict[str, object]] = {
        layer: {"count": 0, "oldest": None} for layer in PRIORITY_LAYER_ORDER
    }

    for job in jobs:
        priority = getattr(job, "priority", 50)
        created_at = getattr(job, "created_at", None)

        layer = get_priority_layer(priority)
        stats[layer]["count"] = int(stats[layer]["count"]) + 1  # type: ignore[arg-type]

        if created_at:
            current_oldest = stats[layer]["oldest"]
            if current_oldest is None or created_at < current_oldest:
                stats[layer]["oldest"] = created_at

    return stats


def sort_jobs_by_stratified_priority(
    jobs: list[object],
    *,
    now: object | None = None,
    aging_enabled: bool = True,
) -> list[object]:
    """Sort jobs by stratified priority (layer, then effective priority, then age).

    Args:
        jobs: List of job objects
        now: Current datetime (for aging calculation)
        aging_enabled: Whether to apply aging bonus

    Returns:
        Sorted list of jobs (highest priority first)

    Sorting key:
        1. Priority layer (critical > high > normal > low > batch)
        2. Effective priority (with aging)
        3. Created time (older first)
        4. Job ID (stable tiebreaker)
    """
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

    def sort_key(job: object) -> tuple[int, int, object, str]:
        priority = getattr(job, "priority", 50)
        created_at = getattr(job, "created_at", now)
        job_id = getattr(job, "job_id", "")

        # Calculate effective priority with aging
        if aging_enabled and isinstance(created_at, datetime) and isinstance(now, datetime):
            wait_time = (now - created_at).total_seconds()
            effective_priority = calculate_effective_priority(priority, wait_time)
        else:
            effective_priority = priority

        # Get layer order (lower number = higher priority)
        layer = get_priority_layer(effective_priority)
        layer_order = PRIORITY_LAYER_ORDER.index(layer) if layer in PRIORITY_LAYER_ORDER else 999

        return (
            layer_order,  # Layer (critical=0, high=1, ...)
            -effective_priority,  # Effective priority (higher first)
            created_at,  # Created time (older first)
            job_id,  # Stable tiebreaker
        )

    return sorted(jobs, key=sort_key)


# ============================================================================
# Configuration
# ============================================================================

# Default aging configuration
DEFAULT_AGING_CONFIG = {
    "enabled": True,
    "interval_seconds": 300,  # 5 minutes
    "bonus_per_interval": 1,
    "max_bonus": 20,
}

# Default tenant quota (jobs per scheduling round)
DEFAULT_TENANT_QUOTA = 10

# Starvation prevention threshold
STARVATION_THRESHOLD_SECONDS = 3600  # 1 hour
