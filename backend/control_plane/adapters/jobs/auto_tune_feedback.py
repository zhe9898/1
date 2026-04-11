"""Job lifecycle to scheduler auto-tune feedback bridge."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.scheduling_decision import SchedulingDecision
from backend.runtime.scheduling.scheduler_auto_tune_audit import write_auto_tune_audit_log

logger = logging.getLogger(__name__)

_TUNER_PERSIST_EVERY_N = 50


def _normalize_score_breakdown(raw: Mapping[str, object] | None) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        normalized[key] = int(value)
    return normalized


async def _resolve_score_breakdown(
    db: AsyncSession,
    *,
    tenant_id: str,
    job_id: str,
    scheduling_decision_id: int | None,
) -> dict[str, int]:
    if scheduling_decision_id is None:
        return {}
    result = await db.execute(
        select(SchedulingDecision).where(
            SchedulingDecision.id == scheduling_decision_id,
            SchedulingDecision.tenant_id == tenant_id,
        )
    )
    decision = result.scalar_one_or_none()
    if decision is None:
        return {}
    placements = getattr(decision, "placements_json", None)
    if not isinstance(placements, list):
        return {}
    for placement in placements:
        if not isinstance(placement, Mapping):
            continue
        if str(placement.get("job_id") or "") != job_id:
            continue
        return _normalize_score_breakdown(placement.get("breakdown"))
    return {}


async def record_job_outcome_for_tuner(
    db: AsyncSession,
    *,
    job: object,
    attempt: object,
    node_id: str,
    success: bool,
    now: datetime.datetime,
) -> None:
    """Record job execution feedback into the scheduler tuner and audit flow."""

    from backend.runtime.scheduling.governance_facade import get_governance_facade
    from backend.runtime.scheduling.scheduler_auto_tune import OutcomeSignal, get_scheduler_tuner

    started = getattr(job, "started_at", None)
    latency_ms = (now - started).total_seconds() * 1000.0 if started else 0.0
    score_breakdown = await _resolve_score_breakdown(
        db,
        tenant_id=str(getattr(job, "tenant_id", "default") or "default"),
        job_id=str(getattr(job, "job_id", "") or ""),
        scheduling_decision_id=getattr(attempt, "scheduling_decision_id", None),
    )
    signal = OutcomeSignal(
        job_id=str(getattr(job, "job_id", "") or ""),
        node_id=node_id,
        kind=str(getattr(job, "kind", "unknown") or "unknown"),
        strategy=str(getattr(job, "scheduling_strategy", None) or "spread"),
        tenant_id=str(getattr(job, "tenant_id", "default") or "default"),
        score_breakdown=score_breakdown,
        success=success,
        latency_ms=latency_ms,
        retry_count=int(getattr(job, "retry_count", 0) or 0),
        node_utilisation=0.0,
        timestamp=now,
    )

    tuner = get_scheduler_tuner()
    audit_record = tuner.record_outcome(signal)
    if audit_record is None:
        return

    should_persist_state = audit_record.total_signals > 0 and audit_record.total_signals % _TUNER_PERSIST_EVERY_N == 0
    try:
        await write_auto_tune_audit_log(
            db,
            audit_record,
            source="job_lifecycle",
            persisted_state=should_persist_state,
            extra_details={
                "attempt_no": int(getattr(attempt, "attempt_no", 0) or 0),
                "scheduling_decision_id": getattr(attempt, "scheduling_decision_id", None),
            },
        )
        if should_persist_state:
            await get_governance_facade().save_tuner_state(db)
    except Exception:
        tuner.restore_audit_record(audit_record)
        raise


def log_tuner_feedback_failure(job_id: str) -> None:
    from backend.runtime.scheduling.scheduling_resilience import SchedulingMetrics

    SchedulingMetrics.record_tuner_failure()
    logger.warning("Failed to record tuner outcome for job %s", job_id, exc_info=True)
