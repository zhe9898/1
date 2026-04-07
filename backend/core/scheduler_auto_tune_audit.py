"""Audit projections for scheduler auto-tune EMA feedback."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.audit_logging import write_audit_log
from backend.core.scheduler_auto_tune_state import AutoTuneAuditRecord

AUTO_TUNE_AUDIT_ACTION = "scheduler_auto_tune.ema_adjusted"
AUTO_TUNE_AUDIT_RESOURCE_TYPE = "scheduler_auto_tune"
AUTO_TUNE_AUDIT_USERNAME = "system:auto-tune"


def _serialize_dimension_delta(delta: object) -> dict[str, object]:
    from backend.core.scheduler_auto_tune_state import DimensionStateDelta

    if not isinstance(delta, DimensionStateDelta):
        return {}
    return {
        "dimension": delta.dimension,
        "reason": delta.reason,
        "before_multiplier": round(delta.before_multiplier, 6),
        "after_multiplier": round(delta.after_multiplier, 6),
        "before_sample_count": delta.before_sample_count,
        "after_sample_count": delta.after_sample_count,
        "before_success_rate": round(delta.before_success_rate, 6),
        "after_success_rate": round(delta.after_success_rate, 6),
        "before_contribution_ema": round(delta.before_contribution_ema, 6),
        "after_contribution_ema": round(delta.after_contribution_ema, 6),
    }


def _serialize_tracker_delta(delta: object) -> dict[str, object]:
    from backend.core.scheduler_auto_tune_state import TrackerStateDelta

    if not isinstance(delta, TrackerStateDelta):
        return {}
    payload: dict[str, object] = {
        "tracker": delta.tracker,
        "key": delta.key,
        "before_sample_count": delta.before_sample_count,
        "after_sample_count": delta.after_sample_count,
        "before_success_rate": round(delta.before_success_rate, 6),
        "after_success_rate": round(delta.after_success_rate, 6),
        "before_avg_latency_ms": round(delta.before_avg_latency_ms, 6),
        "after_avg_latency_ms": round(delta.after_avg_latency_ms, 6),
    }
    if delta.derived_metric_name is not None:
        payload["derived_metric"] = {
            "name": delta.derived_metric_name,
            "before": None if delta.before_derived_metric is None else round(delta.before_derived_metric, 6),
            "after": None if delta.after_derived_metric is None else round(delta.after_derived_metric, 6),
        }
    return payload


def build_auto_tune_audit_details(
    record: AutoTuneAuditRecord,
    *,
    source: str,
    persisted_state: bool,
    extra_details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    signal = record.signal
    details: dict[str, object] = {
        "source": source,
        "persisted_state": persisted_state,
        "job_id": signal.job_id,
        "node_id": signal.node_id,
        "kind": signal.kind,
        "strategy": signal.strategy,
        "success": signal.success,
        "latency_ms": round(signal.latency_ms, 3),
        "retry_count": signal.retry_count,
        "node_utilisation": round(signal.node_utilisation, 6),
        "timestamp": signal.timestamp.isoformat(),
        "score_breakdown": signal.score_breakdown,
        "total_signals_before": record.previous_total_signals,
        "total_signals_after": record.total_signals,
        "dimension_deltas": [_serialize_dimension_delta(delta) for delta in record.dimension_deltas],
        "tracker_deltas": [_serialize_tracker_delta(delta) for delta in record.tracker_deltas],
        "recommended_strategy_after": record.recommended_strategy_after,
    }
    if extra_details:
        details.update(dict(extra_details))
    return details


async def write_auto_tune_audit_log(
    db: AsyncSession,
    record: AutoTuneAuditRecord,
    *,
    source: str,
    persisted_state: bool,
    extra_details: Mapping[str, object] | None = None,
) -> None:
    await write_audit_log(
        db,
        tenant_id=record.signal.tenant_id,
        action=AUTO_TUNE_AUDIT_ACTION,
        result="success",
        username=AUTO_TUNE_AUDIT_USERNAME,
        resource_type=AUTO_TUNE_AUDIT_RESOURCE_TYPE,
        resource_id=record.signal.job_id,
        details=build_auto_tune_audit_details(
            record,
            source=source,
            persisted_state=persisted_state,
            extra_details=extra_details,
        ),
    )
