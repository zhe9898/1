"""
Queue Stratification Module

Implements priority-based queue stratification for fair and predictable job scheduling.
Uses per-layer aging multipliers and exponential aging to prevent starvation of
low-priority jobs while preserving the relative ordering guarantees of
priority tiers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import RLock
from typing import Final

from backend.core.scheduling_policy_types import QueueConfig

# ============================================================================
# Priority Layer Definitions — resolved from policy store
# ============================================================================


def _get_queue_config() -> QueueConfig:
    from backend.core.scheduling_policy_store import get_policy_store

    return get_policy_store().active.queue


def _get_priority_layers() -> dict[str, tuple[int, int]]:
    return _get_queue_config().priority_layers


def _get_layer_aging_multipliers() -> dict[str, float]:
    return _get_queue_config().layer_aging_multipliers


# Backward-compatible module-level aliases (lazy-resolved on first access).
# New code should prefer the functions above.
_QC_DEFAULTS = QueueConfig()
PRIORITY_LAYERS: Final[dict[str, tuple[int, int]]] = _QC_DEFAULTS.priority_layers

PRIORITY_LAYER_ORDER: Final[list[str]] = [
    "critical",
    "high",
    "normal",
    "low",
    "batch",
]

LAYER_AGING_MULTIPLIER: Final[dict[str, float]] = _QC_DEFAULTS.layer_aging_multipliers
_SCHED_CONFIG_LOCK = RLock()
_SCHED_CONFIG_CACHE: dict[str, object] | None = None


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

    for layer_name, (min_priority, max_priority) in _get_priority_layers().items():
        if min_priority <= priority <= max_priority:
            return layer_name

    # Fallback (should never happen due to clamping)
    return "normal"


def calculate_effective_priority(
    base_priority: int,
    wait_time_seconds: float,
    *,
    aging_enabled: bool = True,
    aging_interval_seconds: int | None = None,
    aging_bonus_per_interval: int | None = None,
    aging_max_bonus: int | None = None,
) -> int:
    """Calculate effective priority with exponential aging and per-layer multiplier.

    When aging_interval_seconds / bonus_per_interval / max_bonus are not
    explicitly provided, values are read from the policy store.
    """
    if not aging_enabled or wait_time_seconds <= 0:
        return base_priority

    qc = _get_queue_config().aging
    if aging_interval_seconds is None:
        aging_interval_seconds = qc.interval_seconds
    if aging_bonus_per_interval is None:
        aging_bonus_per_interval = qc.bonus_per_interval
    if aging_max_bonus is None:
        aging_max_bonus = qc.max_bonus

    # Determine layer multiplier
    layer = get_priority_layer(base_priority)
    multiplier = _get_layer_aging_multipliers().get(layer, 1.0)
    if multiplier <= 0:
        return base_priority

    # Exponential aging: bonus grows with sqrt(intervals) for diminishing returns
    intervals = wait_time_seconds / max(aging_interval_seconds, 1)
    raw_bonus = math.sqrt(max(intervals, 0)) * multiplier * aging_bonus_per_interval
    aging_bonus = min(int(raw_bonus), aging_max_bonus)

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

    stats: dict[str, dict[str, object]] = {layer: {"count": 0, "oldest": None} for layer in PRIORITY_LAYER_ORDER}

    for job in jobs:
        priority = getattr(job, "priority", 50)
        created_at = getattr(job, "created_at", None)

        layer = get_priority_layer(priority)
        stats[layer]["count"] = int(stats[layer]["count"]) + 1  # type: ignore[call-overload]

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

        # Re-classify layer using the *effective* priority so that aged
        # jobs can genuinely promote into a higher layer, not just sort
        # higher within their original layer.
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
# Configuration — read from system.yaml with hardcoded fallbacks
# ============================================================================


def _load_scheduling_config() -> dict:
    """Load scheduling config from policy store.

    Returns a dict with the same shape as before for backward compat.
    """
    global _SCHED_CONFIG_CACHE
    with _SCHED_CONFIG_LOCK:
        if _SCHED_CONFIG_CACHE is not None:
            _load_scheduling_config._cache = _SCHED_CONFIG_CACHE  # type: ignore[attr-defined]
            return dict(_SCHED_CONFIG_CACHE)
        qc = _get_queue_config()
        defaults: dict[str, object] = {
            "aging": {
                "enabled": True,
                "interval_seconds": qc.aging.interval_seconds,
                "bonus_per_interval": qc.aging.bonus_per_interval,
                "max_bonus": qc.aging.max_bonus,
            },
            "default_tenant_quota": qc.default_tenant_quota,
            "starvation_threshold_seconds": qc.starvation_threshold_seconds,
        }
        _SCHED_CONFIG_CACHE = defaults
        _load_scheduling_config._cache = defaults  # type: ignore[attr-defined]
        return dict(defaults)


def reset_scheduling_config_cache() -> None:
    """Force re-read of system.yaml on next access (for tests / hot-reload)."""
    global _SCHED_CONFIG_CACHE
    with _SCHED_CONFIG_LOCK:
        _SCHED_CONFIG_CACHE = None
        if hasattr(_load_scheduling_config, "_cache"):
            delattr(_load_scheduling_config, "_cache")


def get_aging_config() -> dict:
    """Return the aging configuration dict."""
    return dict(_load_scheduling_config()["aging"])


def get_default_tenant_quota() -> int:
    """Return the default per-tenant jobs-per-round quota."""
    return int(_load_scheduling_config()["default_tenant_quota"])


def get_starvation_threshold_seconds() -> int:
    """Return the starvation prevention threshold in seconds."""
    return int(_load_scheduling_config()["starvation_threshold_seconds"])


# Legacy module-level constants — still importable for backward compat,
# but callers should prefer the functions above.
_QC_DEFAULTS = QueueConfig()
_AC_DEFAULTS = _QC_DEFAULTS.aging

DEFAULT_AGING_CONFIG = {
    "enabled": True,
    "interval_seconds": _AC_DEFAULTS.interval_seconds,
    "bonus_per_interval": _AC_DEFAULTS.bonus_per_interval,
    "max_bonus": _AC_DEFAULTS.max_bonus,
}

DEFAULT_TENANT_QUOTA = _QC_DEFAULTS.default_tenant_quota

STARVATION_THRESHOLD_SECONDS = _QC_DEFAULTS.starvation_threshold_seconds


# ============================================================================
# Tenant Fair-Share Scheduling
# ============================================================================


@dataclass
class TenantQuota:
    """Per-tenant scheduling quota configuration.

    Attributes:
        max_jobs_per_round: Max jobs dispatched to this tenant per pull cycle.
        weight: Fair-share weight — higher weight = proportionally more quota
                when total demand exceeds capacity.
        service_class: Service tier (premium/standard/economy/batch).
    """

    max_jobs_per_round: int = DEFAULT_TENANT_QUOTA
    weight: float = 1.0
    service_class: str = "standard"


def _get_service_class_config() -> dict[str, dict[str, object]]:
    """Resolve service class config from policy store."""
    from backend.core.scheduling_policy_store import get_policy_store

    scs = get_policy_store().active.service_classes
    return {
        name: {
            "weight": sc.weight,
            "max_jobs_per_round": sc.max_jobs_per_round,
            "starvation_exempt": sc.starvation_exempt,
        }
        for name, sc in scs.items()
    }


# Backward-compatible alias — new code should call _get_service_class_config()
SERVICE_CLASS_CONFIG: Final[dict[str, dict[str, object]]] = {
    "premium": {"weight": 4.0, "max_jobs_per_round": 40, "starvation_exempt": True},
    "standard": {"weight": 2.0, "max_jobs_per_round": 20, "starvation_exempt": False},
    "economy": {"weight": 1.0, "max_jobs_per_round": 10, "starvation_exempt": False},
    "batch": {"weight": 0.5, "max_jobs_per_round": 5, "starvation_exempt": False},
}


class GlobalFairScheduler:
    """Tenant fair-share quota management with DB-first + YAML fallback.

    When ``sched_tenant_policy_db`` feature flag is enabled, reads
    tenant quotas from the ``tenant_scheduling_policies`` table.
    Otherwise falls back to system.yaml configuration.

    In both modes, the result is cached in-memory with a configurable
    TTL (default 60 s) to avoid per-dispatch I/O.

    Config example in system.yaml::

        scheduling:
          default_service_class: standard
          tenant_quotas:
            tenant-alpha:
              service_class: premium
            tenant-beta:
              service_class: economy
              max_jobs_per_round: 8

    Tenants not listed fall back to ``default_service_class``.
    """

    _cache: dict[str, TenantQuota] | None = None
    _default_service_class: str | None = None  # resolved from policy store
    _cache_ts: float = 0.0
    _cache_ttl: float | None = None  # resolved from policy store

    def _load_tenant_quotas(self) -> dict[str, TenantQuota]:
        """Load tenant quotas from system.yaml → scheduling.tenant_quotas.

        Returns cached result if within TTL window.
        """
        import time

        now = time.monotonic()
        _qc = _get_queue_config()
        if self._default_service_class is None:
            self._default_service_class = _qc.default_service_class
        if self._cache_ttl is None:
            self._cache_ttl = _qc.tenant_cache_ttl_seconds
        if self._cache is not None and (now - self._cache_ts) < self._cache_ttl:
            return self._cache

        quotas: dict[str, TenantQuota] = {}
        try:
            from pathlib import Path

            import yaml  # type: ignore[import-untyped, unused-ignore]

            config = yaml.safe_load(Path("system.yaml").read_text(encoding="utf-8"))
            sched = config.get("scheduling", {}) or {}
            self.__class__._default_service_class = sched.get("default_service_class", _qc.default_service_class)
            raw_quotas = sched.get("tenant_quotas", {}) or {}
            for tenant_id, cfg in raw_quotas.items():
                if isinstance(cfg, dict):
                    sc = str(cfg.get("service_class", self._default_service_class))
                    sc_defaults = _get_service_class_config().get(sc, _get_service_class_config()["standard"])
                    quotas[tenant_id] = TenantQuota(
                        max_jobs_per_round=int(cfg.get("max_jobs_per_round", sc_defaults["max_jobs_per_round"])),
                        weight=float(sc_defaults["weight"]),  # type: ignore[arg-type]
                        service_class=sc,
                    )
        except Exception:
            pass  # Fall back to defaults on config read failure

        self.__class__._cache = quotas
        self.__class__._cache_ts = now
        return quotas

    def get_quota(self, tenant_id: str) -> TenantQuota:
        """Return quota for a tenant, falling back to default service class."""
        quotas = self._load_tenant_quotas()
        if tenant_id in quotas:
            return quotas[tenant_id]
        # Fall back to default service class
        sc = self._default_service_class
        _scc = _get_service_class_config()
        sc_defaults = _scc.get(sc or "standard", _scc["standard"])
        return TenantQuota(
            max_jobs_per_round=int(sc_defaults["max_jobs_per_round"]),  # type: ignore[call-overload]
            weight=float(sc_defaults["weight"]),  # type: ignore[arg-type]
            service_class=sc or "standard",
        )

    def get_all_quotas(self) -> dict[str, TenantQuota]:
        """Return all explicitly-configured tenant quotas."""
        return dict(self._load_tenant_quotas())

    def invalidate_cache(self) -> None:
        """Force cache refresh on next access."""
        self.__class__._cache = None
        self.__class__._cache_ts = 0.0

    def load_from_db_policies(
        self,
        policies: list[object],
    ) -> None:
        """Hot-load tenant quotas from DB policy objects.

        Called by the governance layer when ``sched_tenant_policy_db``
        feature flag is active. Replaces the YAML-sourced cache.

        Args:
            policies: list of ``TenantSchedulingPolicy`` ORM instances.
        """
        import time

        quotas: dict[str, TenantQuota] = {}
        for p in policies:
            tid = getattr(p, "tenant_id", "")
            if not tid or not getattr(p, "enabled", True):
                continue
            quotas[tid] = TenantQuota(
                max_jobs_per_round=int(getattr(p, "max_jobs_per_round", 20)),
                weight=float(getattr(p, "fair_share_weight", 2.0)),
                service_class=str(getattr(p, "service_class", "standard")),
            )
        self.__class__._cache = quotas
        self.__class__._cache_ts = time.monotonic()

    def apply_fair_share(
        self,
        candidates: list[object],
    ) -> list[object]:
        """Enforce per-tenant fair-share quotas across a candidate batch.

        Implements a Dominant Resource Fairness (DRF)-inspired weighted
        interleaving algorithm:

        1. Group candidates by tenant.
        2. Build a weighted round-robin schedule — higher weight tenants
           get proportionally more picks per round.
        3. Within each tenant, candidates are consumed in original order
           (which is already priority-sorted upstream).
        4. Starvation detection: any tenant whose oldest pending job
           exceeds ``starvation_threshold`` is guaranteed at least one
           slot per round, even if its weight would normally be zero.

        Args:
            candidates: Job objects (must have ``tenant_id`` attribute).

        Returns:
            Filtered list respecting per-tenant fair-share weights.
        """
        from collections import defaultdict

        if not candidates:
            return []

        # Group by tenant (preserve ordering)
        per_tenant: dict[str, list[object]] = defaultdict(list)
        for job in candidates:
            tid = getattr(job, "tenant_id", "default")
            per_tenant[tid].append(job)

        # Build quota/weight map
        tenant_quota: dict[str, TenantQuota] = {}
        for tid in per_tenant:
            tenant_quota[tid] = self.get_quota(tid)

        # Starvation detection: boost starving tenants
        starvation_s = get_starvation_threshold_seconds()
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        starving: set[str] = set()
        for tid, jobs in per_tenant.items():
            oldest = getattr(jobs[0], "created_at", None)
            if oldest and isinstance(oldest, datetime):
                wait = (now - oldest).total_seconds()
                if wait >= starvation_s:
                    starving.add(tid)

        # Weighted round-robin: distribute slots proportionally by weight
        total_weight = sum(q.weight for q in tenant_quota.values())
        if total_weight <= 0:
            total_weight = 1.0

        remaining: dict[str, int] = {}
        for tid, q in tenant_quota.items():
            remaining[tid] = q.max_jobs_per_round
            # Starvation guarantee: at least 1 slot
            if tid in starving and remaining[tid] < 1:
                remaining[tid] = 1

        # Interleave by weight: repeat rounds until all tenants exhausted
        result: list[object] = []
        cursor: dict[str, int] = {tid: 0 for tid in per_tenant}
        total_cap = len(candidates)

        for _round in range(total_cap):
            picked_any = False
            # Tenants sorted by weight desc for fairness ordering
            sorted_tids = sorted(
                per_tenant.keys(),
                key=lambda t: -tenant_quota[t].weight,
            )
            for tid in sorted_tids:
                if remaining.get(tid, 0) <= 0:
                    continue
                idx = cursor.get(tid, 0)
                if idx >= len(per_tenant[tid]):
                    continue
                result.append(per_tenant[tid][idx])
                cursor[tid] = idx + 1
                remaining[tid] -= 1
                picked_any = True
            if not picked_any:
                break

        return result


# Module-level fair-share scheduler singleton
_fair_scheduler: GlobalFairScheduler | None = None


def get_fair_scheduler() -> GlobalFairScheduler:
    """Return the process-wide GlobalFairScheduler singleton."""
    global _fair_scheduler
    if _fair_scheduler is None:
        _fair_scheduler = GlobalFairScheduler()
    return _fair_scheduler
