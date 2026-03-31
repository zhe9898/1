"""Scheduling policy data structures — frozen, immutable, typed.

All scheduling-related configuration uses frozen dataclasses to guarantee
immutability at runtime.  This module defines *what* is configurable;
validation lives in ``scheduling_policy_validation`` and runtime
governance lives in ``scheduling_policy_store``.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Final


# =====================================================================
# Per-subsystem frozen configs
# =====================================================================


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """Per-dimension scoring caps — the *maximum* raw score each dimension
    can contribute before the auto-tune multiplier is applied."""

    priority_max: int = 160
    age_max: int = 60
    age_half_life_seconds: int = 1800
    scarcity_max: int = 100
    reliability_max: int = 20
    strategy_max: int = 100
    zone_match_bonus: int = 10
    resource_fit_max: int = 24
    executor_match_bonus: int = 12
    resource_closeness_per_dim: int = 6
    data_locality_bonus: int = 15
    latency_max: int = 10
    latency_default: int = 5
    power_max: int = 15
    thermal_max: int = 10
    affinity_max: int = 20
    sla_urgency_max: int = 30
    batch_co_location_max: int = 15
    batch_per_co_located: int = 5
    load_penalty_max: int = 40
    freshness_penalty_max: int = 15
    failure_penalty: int = 40
    anti_affinity_penalty: int = 50


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry / backoff parameters for job failure recovery."""

    base_delay_seconds: int = 10
    max_delay_seconds: int = 600
    resource_exhausted_multiplier: int = 3
    non_retryable_categories: tuple[str, ...] = ()
    max_exponent: int = 6


@dataclass(frozen=True, slots=True)
class NodeFreshnessPolicy:
    """Node heartbeat freshness thresholds."""

    grace_period_seconds: int = 10
    stale_after_seconds: int = 45


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """Queue depth admission control thresholds."""

    max_pending_per_tenant: int = 1000
    max_total_active: int = 10_000


@dataclass(frozen=True, slots=True)
class PreemptionPolicy:
    """Preemption budget limits."""

    max_per_window: int = 5
    window_seconds: int = 300
    min_priority_diff: int = 40
    max_victim_runtime_seconds: int = 300
    max_victim_progress: float = 0.75
    history_buffer_size: int = 500


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Scheduling backoff for unschedulable jobs."""

    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    max_attempts: int = 50
    cleanup_interval: int = 500
    max_exponent: int = 15


@dataclass(frozen=True, slots=True)
class ResourceReservationConfig:
    """Resource reservation policy thresholds."""

    reserve_pct: float = 0.80
    min_priority: int = 70


@dataclass(frozen=True, slots=True)
class ServiceClassDef:
    """Single service class definition."""

    weight: float = 2.0
    max_jobs_per_round: int = 20
    starvation_exempt: bool = False


@dataclass(frozen=True, slots=True)
class KindDefault:
    """Per-kind default scheduling parameters."""

    default_strategy: str = "spread"
    retry_base_delay_seconds: int | None = None
    retry_max_delay_seconds: int | None = None
    max_retries: int | None = None


# ── Strategy sub-configs ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BinpackConfig:
    """Binpack strategy Gaussian curve parameters."""

    peak_utilization: float = 0.75
    sigma: float = 0.25


@dataclass(frozen=True, slots=True)
class LocalityConfig:
    """Locality strategy scoring weights."""

    data_locality_points: int = 40
    partial_cache_points: int = 8
    network_proximity_points: int = 35
    bandwidth_points: int = 25
    bandwidth_saturation_mbps: float = 1000.0
    non_local_bandwidth_points: int = 10


@dataclass(frozen=True, slots=True)
class PerformanceConfig:
    """Performance strategy reference baselines and scoring weights."""

    ref_cpu: int = 8
    ref_memory_mb: int = 16384
    ref_gpu_vram_mb: int = 8192
    ref_bandwidth_mbps: int = 1000
    reliability_weight: int = 30
    cpu_weight: int = 12
    memory_weight: int = 10
    gpu_weight: int = 8
    storage_weight: int = 5
    thermal_cool: int = 15
    thermal_normal: int = 10
    thermal_warm: int = 5
    bandwidth_weight: int = 15
    power_headroom_weight: int = 5


@dataclass(frozen=True, slots=True)
class BalancedWeights:
    """Balanced strategy blend weights (spread, locality, performance)."""

    default: tuple[float, float, float] = (0.45, 0.25, 0.30)
    locality_gpu: tuple[float, float, float] = (0.20, 0.40, 0.40)
    locality_only: tuple[float, float, float] = (0.25, 0.50, 0.25)
    compute_heavy: tuple[float, float, float] = (0.25, 0.20, 0.55)


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """All strategy scoring parameters."""

    binpack: BinpackConfig = field(default_factory=BinpackConfig)
    locality: LocalityConfig = field(default_factory=LocalityConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    balanced: BalancedWeights = field(default_factory=BalancedWeights)
    anti_affinity_penalty: int = 50


# ── Queue stratification config ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class AgingConfig:
    """Priority aging parameters for queue stratification."""

    interval_seconds: int = 300
    bonus_per_interval: int = 1
    max_bonus: int = 20


@dataclass(frozen=True, slots=True)
class QueueConfig:
    """Queue stratification parameters."""

    aging: AgingConfig = field(default_factory=AgingConfig)
    default_tenant_quota: int = 10
    starvation_threshold_seconds: int = 3600
    priority_layers: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "critical": (90, 100),
        "high": (70, 89),
        "normal": (40, 69),
        "low": (20, 39),
        "batch": (0, 19),
    })
    layer_aging_multipliers: dict[str, float] = field(default_factory=lambda: {
        "critical": 0.0,
        "high": 0.5,
        "normal": 1.0,
        "low": 1.5,
        "batch": 2.0,
    })
    tenant_cache_ttl_seconds: float = 60.0
    default_service_class: str = "standard"


# ── Solver / dispatch / business sub-configs ─────────────────────


@dataclass(frozen=True, slots=True)
class SolverConfig:
    """Global placement solver scoring bonuses."""

    spread_bonus: int = 30
    binpack_bonus: int = 25
    affinity_bonus: int = 20
    locality_bonus: int = 15


@dataclass(frozen=True, slots=True)
class PriorityBoostConfig:
    """Priority boosting parameters for business scheduling."""

    default_priority: int = 50
    parent_inheritance_bonus: int = 10
    deadline_urgency_max: int = 30
    deadline_half_life_seconds: int = 7200
    sla_threshold_ratio: float = 0.8
    sla_breach_bonus: int = 20


@dataclass(frozen=True, slots=True)
class SLARiskConfig:
    """SLA risk classification thresholds."""

    default_estimated_duration_s: int = 300
    critical_threshold: float = 0.9
    high_threshold: float = 0.7
    medium_threshold: float = 0.5
    low_threshold: float = 0.3


@dataclass(frozen=True, slots=True)
class BatchScoringConfig:
    """Batch co-location scoring parameters."""

    score_per_member: int = 10
    max_score: int = 100


@dataclass(frozen=True, slots=True)
class AutoTuneConfig:
    """Self-learning auto-tune guardrails and EMA parameters."""

    min_multiplier: float = 0.3
    max_multiplier: float = 3.0
    learning_rate: float = 0.05
    decay_rate: float = 0.005
    min_samples: int = 20
    history_window: int = 2000
    node_ema_alpha: float = 0.1
    node_min_samples: int = 5
    node_bias_center: float = 0.5
    node_bias_scale: float = 40.0
    kind_ema_alpha: float = 0.1
    kind_min_samples: int = 10
    strategy_ema_alpha: float = 0.08
    strategy_min_samples: int = 30
    decay_interval_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class DispatchConfig:
    """Job dispatch / pull query parameters."""

    attempt_lookback_hours: int = 24
    candidate_multiplier: int = 40
    candidate_min: int = 40
    candidate_max: int = 200
    burst_throttle_divisor: int = 2
    burst_throttle_floor: int = 10
    default_reliability_score: float = 0.85
    dlq_scan_limit: int = 50


@dataclass(frozen=True, slots=True)
class TopologySpreadConfig:
    """Topology spread policy defaults."""

    max_skew: int = 2
    penalty_per_skew: int = 8
    max_penalty: int = 40


# =====================================================================
# Composite policy — single source of truth
# =====================================================================

_DEFAULT_SERVICE_CLASSES: Final[dict[str, ServiceClassDef]] = {
    "premium": ServiceClassDef(weight=4.0, max_jobs_per_round=40, starvation_exempt=True),
    "standard": ServiceClassDef(weight=2.0, max_jobs_per_round=20),
    "economy": ServiceClassDef(weight=1.0, max_jobs_per_round=10),
    "batch": ServiceClassDef(weight=0.5, max_jobs_per_round=5),
}


@dataclass(frozen=True)
class SchedulingPolicy:
    """The complete set of externalisable scheduling parameters."""

    scoring: ScoringWeights = field(default_factory=ScoringWeights)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    freshness: NodeFreshnessPolicy = field(default_factory=NodeFreshnessPolicy)
    admission: AdmissionPolicy = field(default_factory=AdmissionPolicy)
    preemption: PreemptionPolicy = field(default_factory=PreemptionPolicy)
    backoff: BackoffPolicy = field(default_factory=BackoffPolicy)
    resource_reservation: ResourceReservationConfig = field(
        default_factory=ResourceReservationConfig,
    )
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    service_classes: dict[str, ServiceClassDef] = field(
        default_factory=lambda: dict(_DEFAULT_SERVICE_CLASSES),
    )
    kind_defaults: dict[str, KindDefault] = field(default_factory=dict)
    default_strategy: str = "spread"
    solver: SolverConfig = field(default_factory=SolverConfig)
    priority_boost: PriorityBoostConfig = field(default_factory=PriorityBoostConfig)
    sla_risk: SLARiskConfig = field(default_factory=SLARiskConfig)
    batch_scoring: BatchScoringConfig = field(default_factory=BatchScoringConfig)
    auto_tune: AutoTuneConfig = field(default_factory=AutoTuneConfig)
    dispatch: DispatchConfig = field(default_factory=DispatchConfig)
    topology_spread: TopologySpreadConfig = field(default_factory=TopologySpreadConfig)


# =====================================================================
# Version metadata
# =====================================================================

MAX_HISTORY: Final[int] = 50


@dataclass(frozen=True, slots=True)
class PolicyVersion:
    """Immutable record of a policy revision."""

    version: int
    policy: SchedulingPolicy
    applied_at: datetime.datetime
    applied_by: str
    reason: str
    diff_summary: dict[str, Any] = field(default_factory=dict)
