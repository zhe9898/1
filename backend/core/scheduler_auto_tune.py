"""Scheduler Auto-Tune — closed-loop self-learning weight adjustment.

Addresses the gap between static scoring weights and real-world outcomes.
The scheduler previously used hardcoded bonuses/penalties in job_scoring.py
with no feedback from actual placement success/failure.

Architecture:

1. **OutcomeSignal** — structured placement outcome (success, latency,
   retry count, node utilisation at completion).
2. **TuningDimension** — each scoreable factor that can be adjusted
   (priority, age, scarcity, reliability, strategy, zone, resource_fit,
   data_locality, latency, power, thermal, affinity, sla_urgency, batch).
3. **AdaptiveWeightStore** — per-dimension learned multiplier using
   Exponential Moving Average (EMA) with configurable learning rate.
4. **SchedulerTuner** — orchestrator that ingests signals, updates the
   store, enforces guardrails, and exposes adjustment queries.

Safety guardrails:
- Multiplier clamped to [MIN_MULTIPLIER, MAX_MULTIPLIER] (default 0.3–3.0)
- Periodic decay toward 1.0 prevents overfitting on transient anomalies
- Minimum sample threshold before adjustments take effect (cold start)
- Feature-flag gated — can be disabled at runtime via governance

Integration:
- ``score_job_for_node`` calls ``tuner.get_adjustment(dim)`` per dimension
- ``dispatch.py`` calls ``tuner.record_outcome(signal)`` on job completion
- ``governance_facade`` exposes ``tuner_snapshot`` and ``reset_tuner``

Benchmarked against:
- Kubernetes scheduler plugins ``PostBind`` feedback (KEP-624)
- HashiCorp Nomad ``spread`` target auto-rebalance
- Apache YARN ``FairScheduler`` weight learning
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.core.scheduling_policy_types import AutoTuneConfig

logger = logging.getLogger(__name__)

# DB key used to persist / restore learned weights across restarts.
_TUNER_STATE_CONFIG_KEY = "scheduler_tuner_weights"


# =====================================================================
# Tunable dimensions — one per scoring factor in job_scoring.py
# =====================================================================


class TuningDimension(str, Enum):
    """Each dimension corresponds to a bonus/penalty in score_job_for_node."""

    PRIORITY = "priority"
    AGE = "age"
    SCARCITY = "scarcity"
    RELIABILITY = "reliability"
    STRATEGY = "strategy"
    ZONE = "zone"
    RESOURCE_FIT = "resource_fit"
    DATA_LOCALITY = "data_locality"
    LATENCY = "latency"
    POWER = "power"
    THERMAL = "thermal"
    AFFINITY = "affinity"
    SLA_URGENCY = "sla_urgency"
    BATCH = "batch"
    LOAD_PENALTY = "load_penalty"
    FRESHNESS_PENALTY = "freshness_penalty"
    FAILURE_PENALTY = "failure_penalty"


# =====================================================================
# Outcome signal — what happened after placement
# =====================================================================


@dataclass(frozen=True, slots=True)
class OutcomeSignal:
    """A single placement outcome fed back into the tuner."""

    job_id: str
    node_id: str
    kind: str
    strategy: str
    tenant_id: str
    # The score breakdown at placement time
    score_breakdown: dict[str, int]
    # Outcome
    success: bool  # completed vs failed/timeout
    latency_ms: float  # wall-clock dispatch-to-completion
    retry_count: int  # how many retries before this attempt
    # Context at completion
    node_utilisation: float  # active_lease / max_concurrency at completion
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


# =====================================================================
# Adaptive weight store — per-dimension EMA multiplier
# =====================================================================

# Guardrail constants — read from policy store at init time


def _get_auto_tune_config() -> AutoTuneConfig:
    from backend.core.scheduling_policy_store import get_policy_store

    return get_policy_store().active.auto_tune


# Module-level aliases resolved lazily (backward compat for direct refs)
def _min_multiplier() -> float:
    return _get_auto_tune_config().min_multiplier


def _max_multiplier() -> float:
    return _get_auto_tune_config().max_multiplier


# Backward-compatible module-level constants from AutoTuneConfig defaults.
# Tests and external callers may import these; values come from the dataclass
# defaults so they stay in-sync with policy-store configuration.
from backend.core.scheduling_policy_types import AutoTuneConfig as _ATCDefaults  # noqa: E402

_atc_defaults = _ATCDefaults()
MIN_MULTIPLIER: float = _atc_defaults.min_multiplier
MAX_MULTIPLIER: float = _atc_defaults.max_multiplier
DEFAULT_LEARNING_RATE: float = _atc_defaults.learning_rate
DEFAULT_DECAY_RATE: float = _atc_defaults.decay_rate
MIN_SAMPLES_BEFORE_ADJUST: int = _atc_defaults.min_samples
del _atc_defaults


@dataclass
class _DimensionState:
    """Per-dimension learned state."""

    multiplier: float = 1.0
    sample_count: int = 0
    success_rate: float = 0.0  # EMA of success for signals where dim > 0
    contribution_ema: float = 0.0  # EMA of normalised contribution to score
    last_updated: float = field(default_factory=time.monotonic)


class AdaptiveWeightStore:
    """Stores per-dimension learned multipliers.

    Update rule (EMA):
        If a dimension was a strong contributor (high % of total score)
        in placements that FAILED, its multiplier is nudged DOWN.
        If strong contributor in SUCCESS, nudged UP (toward 1.0 or above).

    The learning signal is:
        reward = +1 (success) or -1 (failure)
        contribution_ratio = dim_score / max(total_score, 1)
        delta = learning_rate * reward * contribution_ratio
        multiplier = clamp(multiplier + delta, MIN, MAX)
    """

    def __init__(self, *, learning_rate: float | None = None) -> None:
        self._states: dict[str, _DimensionState] = {}
        if learning_rate is None:
            learning_rate = _get_auto_tune_config().learning_rate
        self.learning_rate = learning_rate
        self._init_all_dimensions()

    def _init_all_dimensions(self) -> None:
        for dim in TuningDimension:
            self._states[dim.value] = _DimensionState()

    def update(self, breakdown: dict[str, int], success: bool) -> None:
        """Ingest one outcome and update EMA multipliers."""
        total = sum(abs(v) for v in breakdown.values()) or 1
        reward = 1.0 if success else -1.0

        for key, raw_value in breakdown.items():
            state = self._states.get(key)
            if state is None:
                continue
            contribution = abs(raw_value) / total
            # EMA update of contribution strength
            state.contribution_ema = (1 - self.learning_rate) * state.contribution_ema + self.learning_rate * contribution
            # EMA update of success rate
            state.success_rate = (1 - self.learning_rate) * state.success_rate + self.learning_rate * (1.0 if success else 0.0)
            state.sample_count += 1
            # Only adjust after cold-start threshold
            if state.sample_count < _get_auto_tune_config().min_samples:
                continue
            # Delta proportional to contribution and outcome
            delta = self.learning_rate * reward * contribution
            state.multiplier = _clamp(state.multiplier + delta)
            state.last_updated = time.monotonic()

    def get(self, dimension: str) -> float:
        """Return current multiplier for a dimension (default 1.0)."""
        state = self._states.get(dimension)
        if state is None or state.sample_count < _get_auto_tune_config().min_samples:
            return 1.0
        return state.multiplier

    def decay_toward_baseline(self, rate: float | None = None) -> None:
        """Nudge all multipliers toward 1.0 to prevent drift."""
        if rate is None:
            rate = _get_auto_tune_config().decay_rate
        for state in self._states.values():
            if state.multiplier > 1.0:
                state.multiplier = max(1.0, state.multiplier - rate)
            elif state.multiplier < 1.0:
                state.multiplier = min(1.0, state.multiplier + rate)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return all dimension states for diagnostics."""
        return {
            key: {
                "multiplier": round(s.multiplier, 4),
                "sample_count": s.sample_count,
                "success_rate": round(s.success_rate, 4),
                "contribution_ema": round(s.contribution_ema, 4),
                "active": s.sample_count >= _get_auto_tune_config().min_samples,
            }
            for key, s in self._states.items()
        }

    def reset(self) -> None:
        """Clear all learned state — revert to baseline."""
        self._init_all_dimensions()


def _clamp(value: float) -> float:
    atc = _get_auto_tune_config()
    return max(atc.min_multiplier, min(atc.max_multiplier, value))


# =====================================================================
# Node performance tracker — per-node success/latency EMA
# =====================================================================


@dataclass
class _NodePerf:
    success_rate: float = 1.0
    avg_latency_ms: float = 0.0
    sample_count: int = 0


class NodePerformanceTracker:
    """Track per-node placement success rate and latency.

    Used to apply a learned reliability bias independent of the static
    ``reliability_score`` from heartbeat-based metrics.
    """

    _EMA_ALPHA: float = 0.1  # overwritten in __init__

    def __init__(self) -> None:
        atc = _get_auto_tune_config()
        self._EMA_ALPHA = atc.node_ema_alpha
        self._nodes: dict[str, _NodePerf] = defaultdict(_NodePerf)

    def record(self, node_id: str, success: bool, latency_ms: float) -> None:
        perf = self._nodes[node_id]
        perf.sample_count += 1
        perf.success_rate = (1 - self._EMA_ALPHA) * perf.success_rate + self._EMA_ALPHA * (1.0 if success else 0.0)
        perf.avg_latency_ms = (1 - self._EMA_ALPHA) * perf.avg_latency_ms + self._EMA_ALPHA * latency_ms

    def get_bias(self, node_id: str) -> float:
        """Return a scoring bias (-20 to +20) based on learned node performance.

        Nodes with high success rates get positive bias; failing nodes
        get penalised beyond the static reliability_score.
        """
        perf = self._nodes.get(node_id)
        atc = _get_auto_tune_config()
        if perf is None or perf.sample_count < atc.node_min_samples:
            return 0.0
        # Map success_rate (0.0–1.0) to bias (-20 to +20)
        return round((perf.success_rate - atc.node_bias_center) * atc.node_bias_scale, 2)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            nid: {
                "success_rate": round(p.success_rate, 4),
                "avg_latency_ms": round(p.avg_latency_ms, 2),
                "sample_count": p.sample_count,
                "bias": self.get_bias(nid),
            }
            for nid, p in self._nodes.items()
        }

    def reset(self) -> None:
        self._nodes.clear()


# =====================================================================
# Kind performance tracker — per-kind success EMA
# =====================================================================


class KindPerformanceTracker:
    """Track per-kind success rates to detect systematically failing kinds."""

    _EMA_ALPHA: float = 0.1  # overwritten in __init__

    def __init__(self) -> None:
        atc = _get_auto_tune_config()
        self._EMA_ALPHA = atc.kind_ema_alpha
        self._kinds: dict[str, _NodePerf] = defaultdict(_NodePerf)

    def record(self, kind: str, success: bool, latency_ms: float) -> None:
        perf = self._kinds[kind]
        perf.sample_count += 1
        perf.success_rate = (1 - self._EMA_ALPHA) * perf.success_rate + self._EMA_ALPHA * (1.0 if success else 0.0)
        perf.avg_latency_ms = (1 - self._EMA_ALPHA) * perf.avg_latency_ms + self._EMA_ALPHA * latency_ms

    def get_risk(self, kind: str) -> float:
        """Return kind failure risk (0.0=safe, 1.0=always fails)."""
        perf = self._kinds.get(kind)
        atc = _get_auto_tune_config()
        if perf is None or perf.sample_count < atc.kind_min_samples:
            return 0.0
        return round(1.0 - perf.success_rate, 4)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            k: {
                "success_rate": round(p.success_rate, 4),
                "avg_latency_ms": round(p.avg_latency_ms, 2),
                "sample_count": p.sample_count,
                "risk": self.get_risk(k),
            }
            for k, p in self._kinds.items()
        }

    def reset(self) -> None:
        self._kinds.clear()


# =====================================================================
# Strategy effectiveness tracker
# =====================================================================


class StrategyEffectivenessTracker:
    """Track which scheduling strategies produce the best outcomes."""

    _EMA_ALPHA: float = 0.08  # overwritten in __init__

    def __init__(self) -> None:
        atc = _get_auto_tune_config()
        self._EMA_ALPHA = atc.strategy_ema_alpha
        self._strategies: dict[str, _NodePerf] = defaultdict(_NodePerf)

    def record(self, strategy: str, success: bool, latency_ms: float) -> None:
        perf = self._strategies[strategy]
        perf.sample_count += 1
        perf.success_rate = (1 - self._EMA_ALPHA) * perf.success_rate + self._EMA_ALPHA * (1.0 if success else 0.0)
        perf.avg_latency_ms = (1 - self._EMA_ALPHA) * perf.avg_latency_ms + self._EMA_ALPHA * latency_ms

    def recommend(self) -> str | None:
        """Return strategy with highest success rate, or None if insufficient data."""
        best_strategy = None
        best_rate = 0.0
        for name, perf in self._strategies.items():
            atc = _get_auto_tune_config()
            if perf.sample_count >= atc.strategy_min_samples and perf.success_rate > best_rate:
                best_rate = perf.success_rate
                best_strategy = name
        return best_strategy

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            s: {
                "success_rate": round(p.success_rate, 4),
                "avg_latency_ms": round(p.avg_latency_ms, 2),
                "sample_count": p.sample_count,
            }
            for s, p in self._strategies.items()
        }

    def reset(self) -> None:
        self._strategies.clear()


# =====================================================================
# SchedulerTuner — main orchestrator
# =====================================================================


class SchedulerTuner:
    """Central self-learning engine for scheduler weight auto-tuning.

    Lifecycle:
    1. Each job completion/failure → ``record_outcome(signal)``
    2. Each scoring call → ``get_adjustment(dimension)``
    3. Periodic background → ``decay()``
    4. Admin diagnostics → ``snapshot()``
    """

    def __init__(
        self,
        *,
        learning_rate: float | None = None,
        decay_rate: float | None = None,
        enabled: bool = True,
    ) -> None:
        atc = _get_auto_tune_config()
        _lr = learning_rate if learning_rate is not None else atc.learning_rate
        _dr = decay_rate if decay_rate is not None else atc.decay_rate
        self.weights = AdaptiveWeightStore(learning_rate=_lr)
        self.node_tracker = NodePerformanceTracker()
        self.kind_tracker = KindPerformanceTracker()
        self.strategy_tracker = StrategyEffectivenessTracker()
        self._decay_rate = _dr
        self._enabled = enabled
        self._total_signals: int = 0
        self._recent_signals: deque[OutcomeSignal] = deque(maxlen=atc.history_window)
        self._last_decay: float = time.monotonic()
        self._decay_interval_s: float = atc.decay_interval_seconds

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = value
        if not value:
            logger.info("scheduler auto-tune disabled")
        else:
            logger.info("scheduler auto-tune enabled")

    def record_outcome(self, signal: OutcomeSignal) -> None:
        """Ingest a placement outcome and update all trackers."""
        if not self._enabled:
            return

        self._total_signals += 1
        self._recent_signals.append(signal)

        # Update dimension weights
        self.weights.update(signal.score_breakdown, signal.success)

        # Update node performance
        self.node_tracker.record(signal.node_id, signal.success, signal.latency_ms)

        # Update kind performance
        self.kind_tracker.record(signal.kind, signal.success, signal.latency_ms)

        # Update strategy effectiveness
        self.strategy_tracker.record(signal.strategy, signal.success, signal.latency_ms)

        # Auto-decay check
        now_mono = time.monotonic()
        if now_mono - self._last_decay >= self._decay_interval_s:
            self.decay()
            self._last_decay = now_mono

    def get_adjustment(self, dimension: str) -> float:
        """Return learned multiplier for a scoring dimension.

        Returns 1.0 (no adjustment) if tuning is disabled or
        insufficient samples have been collected.
        """
        if not self._enabled:
            return 1.0
        return self.weights.get(dimension)

    def get_node_bias(self, node_id: str) -> float:
        """Return learned node performance bias (-20 to +20)."""
        if not self._enabled:
            return 0.0
        return self.node_tracker.get_bias(node_id)

    def get_kind_risk(self, kind: str) -> float:
        """Return kind failure risk (0.0–1.0)."""
        if not self._enabled:
            return 0.0
        return self.kind_tracker.get_risk(kind)

    def recommend_strategy(self) -> str | None:
        """Return the best-performing strategy based on outcomes."""
        return self.strategy_tracker.recommend()

    def decay(self) -> None:
        """Nudge all multipliers toward baseline (prevent drift)."""
        self.weights.decay_toward_baseline(self._decay_rate)

    def snapshot(self) -> dict[str, object]:
        """Full diagnostic snapshot for admin/explain endpoints."""
        recent_success = sum(1 for s in self._recent_signals if s.success)
        recent_total = len(self._recent_signals)
        return {
            "enabled": self._enabled,
            "total_signals": self._total_signals,
            "recent_window_size": recent_total,
            "recent_success_rate": (round(recent_success / recent_total, 4) if recent_total else 0.0),
            "dimension_weights": self.weights.snapshot(),
            "node_performance": self.node_tracker.snapshot(),
            "kind_performance": self.kind_tracker.snapshot(),
            "strategy_effectiveness": self.strategy_tracker.snapshot(),
            "recommended_strategy": self.strategy_tracker.recommend(),
        }

    def reset(self) -> None:
        """Clear all learned state — full reset to baseline."""
        self.weights.reset()
        self.node_tracker.reset()
        self.kind_tracker.reset()
        self.strategy_tracker.reset()
        self._total_signals = 0
        self._recent_signals.clear()
        self._last_decay = time.monotonic()
        logger.info("scheduler auto-tune reset to baseline")

    # ── Persistence: load / save dimension weights to system_config ──────

    def state_to_dict(self) -> dict[str, object]:
        """Serialise dimension multipliers and sample counts for storage.

        Only ``AdaptiveWeightStore`` data is persisted — node/kind/strategy
        EMA trackers are intentionally ephemeral (they converge quickly and
        storing per-node history raises privacy/size concerns).
        """
        return {
            "v": 1,
            "saved_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "total_signals": self._total_signals,
            "dimensions": {
                key: {
                    "multiplier": s.multiplier,
                    "sample_count": s.sample_count,
                    "success_rate": s.success_rate,
                    "contribution_ema": s.contribution_ema,
                }
                for key, s in self.weights._states.items()
            },
        }

    def load_from_dict(self, data: dict[str, object]) -> None:
        """Restore dimension state from a previously saved dict.

        Unknown dimensions are silently skipped so that a weight snapshot
        from an older version with fewer dimensions loads cleanly.
        """
        if data.get("v") != 1:
            logger.warning("scheduler_auto_tune: unknown state version %s, skipping load", data.get("v"))
            return
        self._total_signals = int(data.get("total_signals", 0))
        for key, raw in (data.get("dimensions") or {}).items():
            state = self.weights._states.get(key)
            if state is None or not isinstance(raw, dict):
                continue
            state.multiplier = float(raw.get("multiplier", 1.0))
            state.sample_count = int(raw.get("sample_count", 0))
            state.success_rate = float(raw.get("success_rate", 0.0))
            state.contribution_ema = float(raw.get("contribution_ema", 0.0))
        logger.info(
            "scheduler_auto_tune: loaded weights for %d dimensions (%d total signals)",
            len(data.get("dimensions") or {}),
            self._total_signals,
        )

    async def save_state(self, db: AsyncSession) -> None:
        """Persist learned dimension weights to the ``system_config`` table."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from backend.models.feature_flag import SystemConfig

        payload = json.dumps(self.state_to_dict(), separators=(",", ":"))
        stmt = pg_insert(SystemConfig).values(
            key=_TUNER_STATE_CONFIG_KEY,
            value=payload,
            description="Scheduler auto-tune EMA weights (auto-managed)",
            updated_at=datetime.datetime.now(datetime.UTC),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
        )
        await db.execute(stmt)
        await db.commit()
        logger.debug("scheduler_auto_tune: weights persisted to system_config")

    async def load_state(self, db: AsyncSession) -> None:
        """Restore learned dimension weights from the ``system_config`` table.

        Silently skips if the key does not exist (first boot).
        """
        from sqlalchemy import select

        from backend.models.feature_flag import SystemConfig

        result = await db.execute(select(SystemConfig).where(SystemConfig.key == _TUNER_STATE_CONFIG_KEY))
        row: SystemConfig | None = result.scalar_one_or_none()
        if row is None:
            logger.info("scheduler_auto_tune: no persisted weights found, starting from baseline")
            return
        try:
            data: dict[str, object] = json.loads(row.value)
        except (ValueError, TypeError) as exc:
            logger.warning("scheduler_auto_tune: corrupt persisted weights (%s), starting from baseline", exc)
            return
        self.load_from_dict(data)


# ── Module-level singleton ───────────────────────────────────────────

_tuner: SchedulerTuner | None = None


def get_scheduler_tuner() -> SchedulerTuner:
    """Return the process-wide SchedulerTuner singleton."""
    global _tuner
    if _tuner is None:
        _tuner = SchedulerTuner()
    return _tuner
