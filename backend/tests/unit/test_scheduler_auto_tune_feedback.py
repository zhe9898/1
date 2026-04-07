from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.scheduler_auto_tune import OutcomeSignal, SchedulerTuner


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2025, 7, 1, 12, 0, 0)


def _make_signal(**overrides: object) -> OutcomeSignal:
    defaults: dict[str, object] = {
        "job_id": "job-1",
        "node_id": "node-1",
        "kind": "shell.exec",
        "strategy": "spread",
        "tenant_id": "default",
        "score_breakdown": {"priority": 100},
        "success": True,
        "latency_ms": 500.0,
        "retry_count": 0,
        "node_utilisation": 0.5,
        "timestamp": _utcnow(),
    }
    defaults.update(overrides)
    return OutcomeSignal(**defaults)


def test_restore_audit_record_reverts_in_memory_state() -> None:
    tuner = SchedulerTuner()
    record = tuner.record_outcome(_make_signal())

    assert record is not None
    assert tuner._total_signals == 1
    assert tuner.weights._states["priority"].sample_count == 1

    tuner.restore_audit_record(record)

    assert tuner._total_signals == 0
    assert tuner.weights._states["priority"].sample_count == 0
    assert tuner.node_tracker.snapshot() == {}
    assert tuner.kind_tracker.snapshot() == {}
    assert tuner.strategy_tracker.snapshot() == {}


@pytest.mark.asyncio
async def test_resolve_score_breakdown_reads_scheduling_decision() -> None:
    from backend.api.jobs.auto_tune_feedback import _resolve_score_breakdown

    decision = SimpleNamespace(
        placements_json=[
            {"job_id": "job-1", "breakdown": {"priority": 80, "age": 20.7, "ignored": "x"}},
        ],
    )
    result = SimpleNamespace(scalar_one_or_none=lambda: decision)
    db = AsyncMock()
    db.execute.return_value = result

    breakdown = await _resolve_score_breakdown(
        db,
        tenant_id="tenant-1",
        job_id="job-1",
        scheduling_decision_id=7,
    )

    assert breakdown == {"priority": 80, "age": 20}


@pytest.mark.asyncio
async def test_record_job_outcome_audits_and_persists_on_threshold() -> None:
    from backend.api.jobs.auto_tune_feedback import record_job_outcome_for_tuner

    tuner = SchedulerTuner()
    tuner._total_signals = 49
    decision = SimpleNamespace(
        placements_json=[
            {"job_id": "job-42", "breakdown": {"priority": 80, "age": 20}},
        ],
    )
    result = SimpleNamespace(scalar_one_or_none=lambda: decision)
    db = AsyncMock()
    db.execute.return_value = result
    audit_log = AsyncMock()
    governance = SimpleNamespace(save_tuner_state=AsyncMock())
    job = SimpleNamespace(
        job_id="job-42",
        kind="shell.exec",
        scheduling_strategy="spread",
        tenant_id="tenant-1",
        retry_count=2,
        started_at=_utcnow() - datetime.timedelta(seconds=5),
    )
    attempt = SimpleNamespace(attempt_no=4, scheduling_decision_id=7)

    with patch("backend.core.scheduler_auto_tune.get_scheduler_tuner", return_value=tuner):
        with patch("backend.api.jobs.auto_tune_feedback.write_auto_tune_audit_log", audit_log):
            with patch("backend.core.governance_facade.get_governance_facade", return_value=governance):
                await record_job_outcome_for_tuner(
                    db,
                    job=job,
                    attempt=attempt,
                    node_id="node-7",
                    success=True,
                    now=_utcnow(),
                )

    assert tuner._total_signals == 50
    audit_log.assert_awaited_once()
    governance.save_tuner_state.assert_awaited_once_with(db)
    record = audit_log.await_args.args[1]
    assert record.signal.job_id == "job-42"
    assert record.signal.node_id == "node-7"
    assert record.signal.score_breakdown == {"priority": 80, "age": 20}
    assert audit_log.await_args.kwargs["persisted_state"] is True


@pytest.mark.asyncio
async def test_record_job_outcome_rolls_back_tuner_on_audit_failure() -> None:
    from backend.api.jobs.auto_tune_feedback import record_job_outcome_for_tuner

    tuner = SchedulerTuner()
    decision = SimpleNamespace(
        placements_json=[
            {"job_id": "job-9", "breakdown": {"priority": 100}},
        ],
    )
    result = SimpleNamespace(scalar_one_or_none=lambda: decision)
    db = AsyncMock()
    db.execute.return_value = result
    job = SimpleNamespace(
        job_id="job-9",
        kind="connector.invoke",
        scheduling_strategy=None,
        tenant_id="tenant-9",
        retry_count=0,
        started_at=None,
    )
    attempt = SimpleNamespace(attempt_no=1, scheduling_decision_id=9)

    with patch("backend.core.scheduler_auto_tune.get_scheduler_tuner", return_value=tuner):
        with patch(
            "backend.api.jobs.auto_tune_feedback.write_auto_tune_audit_log",
            AsyncMock(side_effect=RuntimeError("audit down")),
        ):
            with pytest.raises(RuntimeError, match="audit down"):
                await record_job_outcome_for_tuner(
                    db,
                    job=job,
                    attempt=attempt,
                    node_id="node-x",
                    success=False,
                    now=_utcnow(),
                )

    assert tuner._total_signals == 0
    assert tuner.weights._states["priority"].sample_count == 0
    assert tuner.node_tracker.snapshot() == {}
