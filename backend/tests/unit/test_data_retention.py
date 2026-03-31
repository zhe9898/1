"""Unit tests for backend.core.data_retention — 数据保留清理。"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.data_retention import (
    TERMINAL_STATUSES,
    _cutoff,
    purge_old_audit_logs,
    purge_old_jobs,
    purge_old_scheduling_decisions,
    run_retention_cycle,
)


# -------------------- helpers --------------------

def _mock_session(scalars_result: list | None = None, rowcount: int = 0) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    if scalars_result is not None:
        result.scalars.return_value.all.return_value = scalars_result
    result.rowcount = rowcount
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


# -------------------- _cutoff --------------------

class TestCutoff:
    def test_cutoff_returns_past_datetime(self) -> None:
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        c = _cutoff(30)
        assert c < now
        diff = now - c
        assert 29 <= diff.days <= 31


# -------------------- purge_old_jobs --------------------

class TestPurgeOldJobs:
    @pytest.mark.asyncio
    async def test_no_jobs_to_purge(self) -> None:
        session = _mock_session(scalars_result=[])
        count = await purge_old_jobs(session)
        assert count == 0
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_purges_terminal_jobs(self) -> None:
        job_ids = ["job-001", "job-002", "job-003"]
        session = _mock_session(scalars_result=job_ids)
        count = await purge_old_jobs(session)
        assert count == 3
        session.commit.assert_awaited_once()
        # 3 execute calls: select IDs, delete attempts, delete jobs
        assert session.execute.await_count == 3


# -------------------- purge_old_scheduling_decisions --------------------

class TestPurgeOldSchedulingDecisions:
    @pytest.mark.asyncio
    async def test_no_decisions_to_purge(self) -> None:
        session = _mock_session(rowcount=0)
        count = await purge_old_scheduling_decisions(session)
        assert count == 0

    @pytest.mark.asyncio
    async def test_purges_old_decisions(self) -> None:
        session = _mock_session(rowcount=42)
        count = await purge_old_scheduling_decisions(session)
        assert count == 42
        session.commit.assert_awaited_once()


# -------------------- purge_old_audit_logs --------------------

class TestPurgeOldAuditLogs:
    @pytest.mark.asyncio
    async def test_no_audit_logs_to_purge(self) -> None:
        session = _mock_session(rowcount=0)
        count = await purge_old_audit_logs(session)
        assert count == 0

    @pytest.mark.asyncio
    async def test_purges_old_audit_logs(self) -> None:
        session = _mock_session(rowcount=99)
        count = await purge_old_audit_logs(session)
        assert count == 99
        session.commit.assert_awaited_once()


# -------------------- run_retention_cycle --------------------

class TestRunRetentionCycle:
    @pytest.mark.asyncio
    async def test_full_cycle_returns_summary(self) -> None:
        # First call: purge_old_jobs select → returns 2 job_ids
        # Second call: purge_old_jobs delete attempts
        # Third call: purge_old_jobs delete jobs
        # Fourth call: purge_old_scheduling_decisions → rowcount=5
        # Fifth call: purge_old_audit_logs → rowcount=10
        call_count = 0
        results: list[MagicMock] = []

        # jobs select result
        r_select = MagicMock()
        r_select.scalars.return_value.all.return_value = ["j1", "j2"]
        results.append(r_select)

        # jobs delete attempts result
        r_del_attempts = MagicMock()
        r_del_attempts.rowcount = 4
        results.append(r_del_attempts)

        # jobs delete jobs result
        r_del_jobs = MagicMock()
        r_del_jobs.rowcount = 2
        results.append(r_del_jobs)

        # scheduling_decisions delete result
        r_sched = MagicMock()
        r_sched.rowcount = 5
        results.append(r_sched)

        # audit_logs delete result
        r_audit = MagicMock()
        r_audit.rowcount = 10
        results.append(r_audit)

        session = AsyncMock()
        session.commit = AsyncMock()
        session.execute = AsyncMock(side_effect=results)

        summary = await run_retention_cycle(session)
        assert summary["jobs"] == 2
        assert summary["scheduling_decisions"] == 5
        assert summary["audit_logs"] == 10

    @pytest.mark.asyncio
    async def test_empty_cycle(self) -> None:
        # All queries return nothing
        r_empty = MagicMock()
        r_empty.scalars.return_value.all.return_value = []
        r_empty.rowcount = 0

        session = AsyncMock()
        session.commit = AsyncMock()
        session.execute = AsyncMock(return_value=r_empty)

        summary = await run_retention_cycle(session)
        assert summary == {"jobs": 0, "scheduling_decisions": 0, "audit_logs": 0}


# -------------------- constants --------------------

class TestConstants:
    def test_terminal_statuses(self) -> None:
        assert "completed" in TERMINAL_STATUSES
        assert "failed" in TERMINAL_STATUSES
        assert "cancelled" in TERMINAL_STATUSES
        assert "pending" not in TERMINAL_STATUSES
        assert "leased" not in TERMINAL_STATUSES
