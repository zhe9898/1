"""Scheduler auto-tune orchestrator and persistence boundary."""

from __future__ import annotations

import datetime
import inspect
import json
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from backend.core.scheduler_auto_tune_state import (
    DEFAULT_DECAY_RATE,
    DEFAULT_LEARNING_RATE,
    MAX_MULTIPLIER,
    MIN_MULTIPLIER,
    MIN_SAMPLES_BEFORE_ADJUST,
    AdaptiveWeightStore,
    AutoTuneAuditRecord,
    DimensionStateDelta,
    KindPerformanceTracker,
    NodePerformanceTracker,
    OutcomeSignal,
    StrategyEffectivenessTracker,
    TrackerStateDelta,
    TuningDimension,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_TUNER_STATE_CONFIG_KEY = "scheduler_tuner_weights"
_tuner: "SchedulerTuner | None" = None


class SchedulerTuner:
    """Central self-learning engine for scheduler weight auto-tuning."""

    def __init__(
        self,
        *,
        learning_rate: float | None = None,
        decay_rate: float | None = None,
        enabled: bool = True,
    ) -> None:
        from backend.core.scheduler_auto_tune_state import _get_auto_tune_config

        atc = _get_auto_tune_config()
        self.weights = AdaptiveWeightStore(
            learning_rate=learning_rate if learning_rate is not None else atc.learning_rate,
        )
        self.node_tracker = NodePerformanceTracker()
        self.kind_tracker = KindPerformanceTracker()
        self.strategy_tracker = StrategyEffectivenessTracker()
        self._decay_rate = decay_rate if decay_rate is not None else atc.decay_rate
        self._enabled = enabled
        self._total_signals = 0
        self._recent_signals: deque[OutcomeSignal] = deque(maxlen=atc.history_window)
        self._last_decay = time.monotonic()
        self._decay_interval_s = atc.decay_interval_seconds

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = value
        logger.info("scheduler auto-tune %s", "enabled" if value else "disabled")

    def record_outcome(self, signal: OutcomeSignal) -> AutoTuneAuditRecord | None:
        """Ingest a placement outcome and return the resulting EMA transitions."""

        if not self._enabled:
            return None

        previous_total_signals = self._total_signals
        previous_last_decay = self._last_decay
        evicted_recent_signal = self._recent_signals[0] if len(self._recent_signals) == self._recent_signals.maxlen else None

        self._total_signals += 1
        self._recent_signals.append(signal)

        dimension_deltas = list(self.weights.update(signal.score_breakdown, signal.success))
        tracker_deltas: list[TrackerStateDelta] = [
            self.node_tracker.record(signal.node_id, signal.success, signal.latency_ms),
            self.kind_tracker.record(signal.kind, signal.success, signal.latency_ms),
            self.strategy_tracker.record(signal.strategy, signal.success, signal.latency_ms),
        ]

        now_mono = time.monotonic()
        if now_mono - self._last_decay >= self._decay_interval_s:
            dimension_deltas.extend(self.decay())
            self._last_decay = now_mono

        return AutoTuneAuditRecord(
            signal=signal,
            previous_total_signals=previous_total_signals,
            total_signals=self._total_signals,
            previous_last_decay=previous_last_decay,
            evicted_recent_signal=evicted_recent_signal,
            dimension_deltas=tuple(dimension_deltas),
            tracker_deltas=tuple(tracker_deltas),
            recommended_strategy_after=self.strategy_tracker.recommend(),
        )

    def restore_audit_record(self, record: AutoTuneAuditRecord) -> None:
        """Rollback in-memory tuner state if audit/persistence fails."""

        for dimension_delta in reversed(record.dimension_deltas):
            self.weights.restore_delta(dimension_delta)
        for tracker_delta in reversed(record.tracker_deltas):
            if tracker_delta.tracker == "node_performance":
                self.node_tracker.restore_delta(tracker_delta)
            elif tracker_delta.tracker == "kind_performance":
                self.kind_tracker.restore_delta(tracker_delta)
            elif tracker_delta.tracker == "strategy_effectiveness":
                self.strategy_tracker.restore_delta(tracker_delta)
        self._total_signals = record.previous_total_signals
        self._last_decay = record.previous_last_decay
        if self._recent_signals and self._recent_signals[-1] == record.signal:
            self._recent_signals.pop()
        if record.evicted_recent_signal is not None:
            self._recent_signals.appendleft(record.evicted_recent_signal)

    def get_adjustment(self, dimension: str) -> float:
        if not self._enabled:
            return 1.0
        return self.weights.get(dimension)

    def get_node_bias(self, node_id: str) -> float:
        if not self._enabled:
            return 0.0
        return self.node_tracker.get_bias(node_id)

    def get_kind_risk(self, kind: str) -> float:
        if not self._enabled:
            return 0.0
        return self.kind_tracker.get_risk(kind)

    def recommend_strategy(self) -> str | None:
        return self.strategy_tracker.recommend()

    def decay(self) -> tuple[DimensionStateDelta, ...]:
        return self.weights.decay_toward_baseline(self._decay_rate)

    def snapshot(self) -> dict[str, object]:
        recent_success = sum(1 for s in self._recent_signals if s.success)
        recent_total = len(self._recent_signals)
        return {
            "enabled": self._enabled,
            "total_signals": self._total_signals,
            "recent_window_size": recent_total,
            "recent_success_rate": round(recent_success / recent_total, 4) if recent_total else 0.0,
            "dimension_weights": self.weights.snapshot(),
            "node_performance": self.node_tracker.snapshot(),
            "kind_performance": self.kind_tracker.snapshot(),
            "strategy_effectiveness": self.strategy_tracker.snapshot(),
            "recommended_strategy": self.strategy_tracker.recommend(),
        }

    def reset(self) -> None:
        self.weights.reset()
        self.node_tracker.reset()
        self.kind_tracker.reset()
        self.strategy_tracker.reset()
        self._total_signals = 0
        self._recent_signals.clear()
        self._last_decay = time.monotonic()
        logger.info("scheduler auto-tune reset to baseline")

    def state_to_dict(self) -> dict[str, object]:
        return {
            "v": 1,
            "saved_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "total_signals": self._total_signals,
            "dimensions": {
                key: {
                    "multiplier": state.multiplier,
                    "sample_count": state.sample_count,
                    "success_rate": state.success_rate,
                    "contribution_ema": state.contribution_ema,
                }
                for key, state in self.weights._states.items()
            },
        }

    def load_from_dict(self, data: dict[str, object]) -> None:
        if data.get("v") != 1:
            logger.warning(
                "scheduler_auto_tune: unknown state version %s, skipping load",
                data.get("v"),
            )
            return
        total_signals = data.get("total_signals", 0)
        self._total_signals = int(total_signals) if isinstance(total_signals, (int, float)) and not isinstance(total_signals, bool) else 0
        raw_dimensions = data.get("dimensions")
        dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
        for key, raw in dimensions.items():
            state = self.weights._states.get(key)
            if state is None or not isinstance(raw, dict):
                continue
            multiplier = raw.get("multiplier", 1.0)
            sample_count = raw.get("sample_count", 0)
            success_rate = raw.get("success_rate", 0.0)
            contribution_ema = raw.get("contribution_ema", 0.0)
            state.multiplier = max(
                MIN_MULTIPLIER,
                min(
                    MAX_MULTIPLIER,
                    float(multiplier) if isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool) else 1.0,
                ),
            )
            state.sample_count = int(sample_count) if isinstance(sample_count, (int, float)) and not isinstance(sample_count, bool) else 0
            state.success_rate = float(success_rate) if isinstance(success_rate, (int, float)) and not isinstance(success_rate, bool) else 0.0
            state.contribution_ema = float(contribution_ema) if isinstance(contribution_ema, (int, float)) and not isinstance(contribution_ema, bool) else 0.0
        logger.info(
            "scheduler_auto_tune: loaded weights for %d dimensions (%d total signals)",
            len(dimensions),
            self._total_signals,
        )

    async def save_state(self, db: AsyncSession) -> None:
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
        await db.flush()
        logger.debug("scheduler_auto_tune: weights persisted to system_config")

    async def load_state(self, db: AsyncSession) -> None:
        from sqlalchemy import select

        from backend.models.feature_flag import SystemConfig

        result = await db.execute(
            select(SystemConfig).where(SystemConfig.key == _TUNER_STATE_CONFIG_KEY),
        )
        scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
        if not callable(scalar_one_or_none):
            logger.warning("scheduler_auto_tune: persisted state query returned an unsupported result object")
            return

        row = scalar_one_or_none()
        if inspect.isawaitable(row):
            row = await row
        if row is None:
            logger.info("scheduler_auto_tune: no persisted weights found, starting from baseline")
            return

        raw_value = getattr(row, "value", None)
        if not isinstance(raw_value, str):
            logger.warning("scheduler_auto_tune: persisted weights payload is not a JSON string, starting from baseline")
            return
        try:
            data = json.loads(raw_value)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "scheduler_auto_tune: corrupt persisted weights (%s), starting from baseline",
                exc,
            )
            return
        if isinstance(data, dict):
            self.load_from_dict(data)


def get_scheduler_tuner() -> SchedulerTuner:
    global _tuner
    if _tuner is None:
        _tuner = SchedulerTuner()
    return _tuner


__all__ = [
    "AdaptiveWeightStore",
    "AutoTuneAuditRecord",
    "DEFAULT_DECAY_RATE",
    "DEFAULT_LEARNING_RATE",
    "DimensionStateDelta",
    "KindPerformanceTracker",
    "MAX_MULTIPLIER",
    "MIN_MULTIPLIER",
    "MIN_SAMPLES_BEFORE_ADJUST",
    "NodePerformanceTracker",
    "OutcomeSignal",
    "SchedulerTuner",
    "StrategyEffectivenessTracker",
    "TrackerStateDelta",
    "TuningDimension",
    "get_scheduler_tuner",
]
