"""State models and EMA trackers for scheduler auto-tune."""

from __future__ import annotations

import datetime
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.scheduling_policy_types import AutoTuneConfig


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


@dataclass(frozen=True, slots=True)
class OutcomeSignal:
    """A single placement outcome fed back into the tuner."""

    job_id: str
    node_id: str
    kind: str
    strategy: str
    tenant_id: str
    score_breakdown: dict[str, int]
    success: bool
    latency_ms: float
    retry_count: int
    node_utilisation: float
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )


@dataclass(frozen=True, slots=True)
class DimensionStateDelta:
    """State transition for one tunable dimension."""

    dimension: str
    reason: str
    before_multiplier: float
    after_multiplier: float
    before_sample_count: int
    after_sample_count: int
    before_success_rate: float
    after_success_rate: float
    before_contribution_ema: float
    after_contribution_ema: float


@dataclass(frozen=True, slots=True)
class TrackerStateDelta:
    """EMA transition for node/kind/strategy trackers."""

    tracker: str
    key: str
    existed_before: bool
    before_sample_count: int
    after_sample_count: int
    before_success_rate: float
    after_success_rate: float
    before_avg_latency_ms: float
    after_avg_latency_ms: float
    derived_metric_name: str | None = None
    before_derived_metric: float | None = None
    after_derived_metric: float | None = None


@dataclass(frozen=True, slots=True)
class AutoTuneAuditRecord:
    """Structured outcome feedback record for persistence and audit."""

    signal: OutcomeSignal
    previous_total_signals: int
    total_signals: int
    previous_last_decay: float
    evicted_recent_signal: OutcomeSignal | None = None
    dimension_deltas: tuple[DimensionStateDelta, ...] = ()
    tracker_deltas: tuple[TrackerStateDelta, ...] = ()
    recommended_strategy_after: str | None = None

    @property
    def has_changes(self) -> bool:
        return bool(self.dimension_deltas or self.tracker_deltas)


def _get_auto_tune_config() -> AutoTuneConfig:
    from backend.kernel.policy.policy_store import get_policy_store

    return get_policy_store().active.auto_tune


def _min_multiplier() -> float:
    return _get_auto_tune_config().min_multiplier


def _max_multiplier() -> float:
    return _get_auto_tune_config().max_multiplier


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
    multiplier: float = 1.0
    sample_count: int = 0
    success_rate: float = 0.0
    contribution_ema: float = 0.0
    last_updated: float = field(default_factory=time.monotonic)


@dataclass
class _NodePerf:
    success_rate: float = 1.0
    avg_latency_ms: float = 0.0
    sample_count: int = 0


def _copy_dimension_state(state: _DimensionState) -> _DimensionState:
    return _DimensionState(
        multiplier=state.multiplier,
        sample_count=state.sample_count,
        success_rate=state.success_rate,
        contribution_ema=state.contribution_ema,
        last_updated=state.last_updated,
    )


def _copy_node_perf(perf: _NodePerf) -> _NodePerf:
    return _NodePerf(
        success_rate=perf.success_rate,
        avg_latency_ms=perf.avg_latency_ms,
        sample_count=perf.sample_count,
    )


def _clamp(value: float) -> float:
    atc = _get_auto_tune_config()
    return max(atc.min_multiplier, min(atc.max_multiplier, value))


def _dimension_delta(
    *,
    dimension: str,
    reason: str,
    before: _DimensionState,
    after: _DimensionState,
) -> DimensionStateDelta:
    return DimensionStateDelta(
        dimension=dimension,
        reason=reason,
        before_multiplier=before.multiplier,
        after_multiplier=after.multiplier,
        before_sample_count=before.sample_count,
        after_sample_count=after.sample_count,
        before_success_rate=before.success_rate,
        after_success_rate=after.success_rate,
        before_contribution_ema=before.contribution_ema,
        after_contribution_ema=after.contribution_ema,
    )


def _tracker_delta(
    *,
    tracker: str,
    key: str,
    existed_before: bool,
    before: _NodePerf,
    after: _NodePerf,
    derived_metric_name: str | None = None,
    before_derived_metric: float | None = None,
    after_derived_metric: float | None = None,
) -> TrackerStateDelta:
    return TrackerStateDelta(
        tracker=tracker,
        key=key,
        existed_before=existed_before,
        before_sample_count=before.sample_count,
        after_sample_count=after.sample_count,
        before_success_rate=before.success_rate,
        after_success_rate=after.success_rate,
        before_avg_latency_ms=before.avg_latency_ms,
        after_avg_latency_ms=after.avg_latency_ms,
        derived_metric_name=derived_metric_name,
        before_derived_metric=before_derived_metric,
        after_derived_metric=after_derived_metric,
    )


class AdaptiveWeightStore:
    """Stores per-dimension learned multipliers."""

    def __init__(self, *, learning_rate: float | None = None) -> None:
        self._states: dict[str, _DimensionState] = {}
        if learning_rate is None:
            learning_rate = _get_auto_tune_config().learning_rate
        self.learning_rate = learning_rate
        self._init_all_dimensions()

    def _init_all_dimensions(self) -> None:
        for dim in TuningDimension:
            self._states[dim.value] = _DimensionState()

    def update(self, breakdown: dict[str, int], success: bool) -> tuple[DimensionStateDelta, ...]:
        total = sum(abs(v) for v in breakdown.values()) or 1
        reward = 1.0 if success else -1.0
        deltas: list[DimensionStateDelta] = []

        for key, raw_value in breakdown.items():
            state = self._states.get(key)
            if state is None:
                continue
            before = _copy_dimension_state(state)
            contribution = abs(raw_value) / total
            state.contribution_ema = (1 - self.learning_rate) * state.contribution_ema + self.learning_rate * contribution
            state.success_rate = (1 - self.learning_rate) * state.success_rate + self.learning_rate * (1.0 if success else 0.0)
            state.sample_count += 1
            if state.sample_count >= _get_auto_tune_config().min_samples:
                delta = self.learning_rate * reward * contribution
                state.multiplier = _clamp(state.multiplier + delta)
            state.last_updated = time.monotonic()
            deltas.append(
                _dimension_delta(
                    dimension=key,
                    reason="feedback",
                    before=before,
                    after=state,
                )
            )
        return tuple(deltas)

    def get(self, dimension: str) -> float:
        state = self._states.get(dimension)
        if state is None or state.sample_count < _get_auto_tune_config().min_samples:
            return 1.0
        return state.multiplier

    def decay_toward_baseline(self, rate: float | None = None) -> tuple[DimensionStateDelta, ...]:
        if rate is None:
            rate = _get_auto_tune_config().decay_rate
        deltas: list[DimensionStateDelta] = []
        for key, state in self._states.items():
            before = _copy_dimension_state(state)
            if state.multiplier > 1.0:
                state.multiplier = max(1.0, state.multiplier - rate)
            elif state.multiplier < 1.0:
                state.multiplier = min(1.0, state.multiplier + rate)
            if state.multiplier != before.multiplier:
                deltas.append(
                    _dimension_delta(
                        dimension=key,
                        reason="decay",
                        before=before,
                        after=state,
                    )
                )
        return tuple(deltas)

    def restore_delta(self, delta: DimensionStateDelta) -> None:
        state = self._states[delta.dimension]
        state.multiplier = delta.before_multiplier
        state.sample_count = delta.before_sample_count
        state.success_rate = delta.before_success_rate
        state.contribution_ema = delta.before_contribution_ema

    def snapshot(self) -> dict[str, dict[str, object]]:
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
        self._init_all_dimensions()


class NodePerformanceTracker:
    """Track per-node placement success rate and latency."""

    _EMA_ALPHA: float = 0.1

    def __init__(self) -> None:
        atc = _get_auto_tune_config()
        self._EMA_ALPHA = atc.node_ema_alpha
        self._nodes: dict[str, _NodePerf] = defaultdict(_NodePerf)

    def _bias_from_perf(self, perf: _NodePerf) -> float:
        atc = _get_auto_tune_config()
        if perf.sample_count < atc.node_min_samples:
            return 0.0
        return round((perf.success_rate - atc.node_bias_center) * atc.node_bias_scale, 2)

    def record(self, node_id: str, success: bool, latency_ms: float) -> TrackerStateDelta:
        existed_before = node_id in self._nodes
        perf = self._nodes[node_id]
        before = _copy_node_perf(perf)
        before_bias = self._bias_from_perf(before)
        perf.sample_count += 1
        perf.success_rate = (1 - self._EMA_ALPHA) * perf.success_rate + self._EMA_ALPHA * (1.0 if success else 0.0)
        perf.avg_latency_ms = (1 - self._EMA_ALPHA) * perf.avg_latency_ms + self._EMA_ALPHA * latency_ms
        after_bias = self._bias_from_perf(perf)
        return _tracker_delta(
            tracker="node_performance",
            key=node_id,
            existed_before=existed_before,
            before=before,
            after=perf,
            derived_metric_name="bias",
            before_derived_metric=before_bias,
            after_derived_metric=after_bias,
        )

    def get_bias(self, node_id: str) -> float:
        perf = self._nodes.get(node_id)
        if perf is None:
            return 0.0
        return self._bias_from_perf(perf)

    def restore_delta(self, delta: TrackerStateDelta) -> None:
        if not delta.existed_before:
            self._nodes.pop(delta.key, None)
            return
        perf = self._nodes[delta.key]
        perf.sample_count = delta.before_sample_count
        perf.success_rate = delta.before_success_rate
        perf.avg_latency_ms = delta.before_avg_latency_ms

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


class KindPerformanceTracker:
    """Track per-kind success rates to detect systematically failing kinds."""

    _EMA_ALPHA: float = 0.1

    def __init__(self) -> None:
        atc = _get_auto_tune_config()
        self._EMA_ALPHA = atc.kind_ema_alpha
        self._kinds: dict[str, _NodePerf] = defaultdict(_NodePerf)

    def _risk_from_perf(self, perf: _NodePerf) -> float:
        atc = _get_auto_tune_config()
        if perf.sample_count < atc.kind_min_samples:
            return 0.0
        return round(1.0 - perf.success_rate, 4)

    def record(self, kind: str, success: bool, latency_ms: float) -> TrackerStateDelta:
        existed_before = kind in self._kinds
        perf = self._kinds[kind]
        before = _copy_node_perf(perf)
        before_risk = self._risk_from_perf(before)
        perf.sample_count += 1
        perf.success_rate = (1 - self._EMA_ALPHA) * perf.success_rate + self._EMA_ALPHA * (1.0 if success else 0.0)
        perf.avg_latency_ms = (1 - self._EMA_ALPHA) * perf.avg_latency_ms + self._EMA_ALPHA * latency_ms
        after_risk = self._risk_from_perf(perf)
        return _tracker_delta(
            tracker="kind_performance",
            key=kind,
            existed_before=existed_before,
            before=before,
            after=perf,
            derived_metric_name="risk",
            before_derived_metric=before_risk,
            after_derived_metric=after_risk,
        )

    def get_risk(self, kind: str) -> float:
        perf = self._kinds.get(kind)
        if perf is None:
            return 0.0
        return self._risk_from_perf(perf)

    def restore_delta(self, delta: TrackerStateDelta) -> None:
        if not delta.existed_before:
            self._kinds.pop(delta.key, None)
            return
        perf = self._kinds[delta.key]
        perf.sample_count = delta.before_sample_count
        perf.success_rate = delta.before_success_rate
        perf.avg_latency_ms = delta.before_avg_latency_ms

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


class StrategyEffectivenessTracker:
    """Track which scheduling strategies produce the best outcomes."""

    _EMA_ALPHA: float = 0.08

    def __init__(self) -> None:
        atc = _get_auto_tune_config()
        self._EMA_ALPHA = atc.strategy_ema_alpha
        self._strategies: dict[str, _NodePerf] = defaultdict(_NodePerf)

    def record(self, strategy: str, success: bool, latency_ms: float) -> TrackerStateDelta:
        existed_before = strategy in self._strategies
        perf = self._strategies[strategy]
        before = _copy_node_perf(perf)
        perf.sample_count += 1
        perf.success_rate = (1 - self._EMA_ALPHA) * perf.success_rate + self._EMA_ALPHA * (1.0 if success else 0.0)
        perf.avg_latency_ms = (1 - self._EMA_ALPHA) * perf.avg_latency_ms + self._EMA_ALPHA * latency_ms
        return _tracker_delta(
            tracker="strategy_effectiveness",
            key=strategy,
            existed_before=existed_before,
            before=before,
            after=perf,
        )

    def recommend(self) -> str | None:
        best_strategy = None
        best_rate = 0.0
        for name, perf in self._strategies.items():
            atc = _get_auto_tune_config()
            if perf.sample_count >= atc.strategy_min_samples and perf.success_rate > best_rate:
                best_rate = perf.success_rate
                best_strategy = name
        return best_strategy

    def restore_delta(self, delta: TrackerStateDelta) -> None:
        if not delta.existed_before:
            self._strategies.pop(delta.key, None)
            return
        perf = self._strategies[delta.key]
        perf.sample_count = delta.before_sample_count
        perf.success_rate = delta.before_success_rate
        perf.avg_latency_ms = delta.before_avg_latency_ms

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
