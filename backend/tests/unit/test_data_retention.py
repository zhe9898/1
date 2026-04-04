"""Unit tests for backend.core.data_retention."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.core.data_retention import (
    TERMINAL_STATUSES,
    _cutoff,
    _list_active_tenant_ids,
    purge_old_audit_logs,
    purge_old_jobs,
    purge_old_scheduling_decisions,
    run_retention_cycle,
)


def _mock_session(scalars_result: list | None = None, rowcount: int = 0) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    if scalars_result is not None:
        result.scalars.return_value.all.return_value = scalars_result
    result.rowcount = rowcount
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


class TestCutoff:
    def test_cutoff_returns_past_datetime(self) -> None:
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        c = _cutoff(30)
        assert c < now
        diff = now - c
        assert 29 <= diff.days <= 31


class TestPurgeOldJobs:
    @pytest.mark.asyncio
    async def test_no_jobs_to_purge(self) -> None:
        session = _mock_session(scalars_result=[])
        count = await purge_old_jobs(session, "tenant-a")
        assert count == 0
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_purges_terminal_jobs(self) -> None:
        session = _mock_session(scalars_result=["job-001", "job-002", "job-003"])
        count = await purge_old_jobs(session, "tenant-a")
        assert count == 3
        session.commit.assert_awaited_once()
        # set tenant context + select + delete attempts + delete jobs
        assert session.execute.await_count == 4


class TestPurgeOldSchedulingDecisions:
    @pytest.mark.asyncio
    async def test_no_decisions_to_purge(self) -> None:
        session = _mock_session(scalars_result=[])
        count = await purge_old_scheduling_decisions(session, "tenant-a")
        assert count == 0

    @pytest.mark.asyncio
    async def test_purges_old_decisions(self) -> None:
        session = _mock_session(scalars_result=list(range(42)))
        count = await purge_old_scheduling_decisions(session, "tenant-a")
        assert count == 42
        session.commit.assert_awaited_once()


class TestPurgeOldAuditLogs:
    @pytest.mark.asyncio
    async def test_no_audit_logs_to_purge(self) -> None:
        session = _mock_session(scalars_result=[])
        count = await purge_old_audit_logs(session, "tenant-a")
        assert count == 0

    @pytest.mark.asyncio
    async def test_purges_old_audit_logs(self) -> None:
        session = _mock_session(scalars_result=list(range(99)))
        count = await purge_old_audit_logs(session, "tenant-a")
        assert count == 99
        session.commit.assert_awaited_once()


class TestRunRetentionCycle:
    @pytest.mark.asyncio
    async def test_full_cycle_returns_aggregated_summary(self) -> None:
        session = AsyncMock()
        with (
            patch("backend.core.data_retention._list_active_tenant_ids", new=AsyncMock(return_value=["tenant-a", "tenant-b"])),
            patch(
                "backend.core.data_retention._run_retention_cycle_for_tenant",
                new=AsyncMock(
                    side_effect=[
                        {"jobs": 2, "scheduling_decisions": 5, "audit_logs": 10},
                        {"jobs": 1, "scheduling_decisions": 0, "audit_logs": 4},
                    ]
                ),
            ) as run_one,
        ):
            summary = await run_retention_cycle(session)

        assert summary == {"jobs": 3, "scheduling_decisions": 5, "audit_logs": 14}
        assert run_one.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_cycle(self) -> None:
        session = AsyncMock()
        with (
            patch("backend.core.data_retention._list_active_tenant_ids", new=AsyncMock(return_value=["tenant-a"])),
            patch(
                "backend.core.data_retention._run_retention_cycle_for_tenant",
                new=AsyncMock(return_value={"jobs": 0, "scheduling_decisions": 0, "audit_logs": 0}),
            ),
        ):
            summary = await run_retention_cycle(session)

        assert summary == {"jobs": 0, "scheduling_decisions": 0, "audit_logs": 0}


class TestListActiveTenantIds:
    @pytest.mark.asyncio
    async def test_returns_default_when_query_fails(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=SQLAlchemyError("db boom"))
        tenant_ids = await _list_active_tenant_ids(session)
        assert tenant_ids == ["default"]


class TestConstants:
    def test_terminal_statuses(self) -> None:
        assert "completed" in TERMINAL_STATUSES
        assert "failed" in TERMINAL_STATUSES
        assert "cancelled" in TERMINAL_STATUSES
        assert "pending" not in TERMINAL_STATUSES
        assert "leased" not in TERMINAL_STATUSES
