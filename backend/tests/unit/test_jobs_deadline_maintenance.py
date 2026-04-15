from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.control_plane.adapters.jobs import deadline_maintenance as dm


@pytest.mark.asyncio
async def test_maybe_schedule_deadline_dlq_sweep_throttles_per_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    dm._reset_deadline_sweep_state_for_tests()
    calls: list[str] = []

    async def _fake_run(tenant_id: str, redis: object | None) -> int:
        del redis
        calls.append(tenant_id)
        return 0

    monkeypatch.setattr(dm, "_run_deadline_dlq_sweep", _fake_run)
    monkeypatch.setattr(dm, "_deadline_sweep_interval_seconds", lambda: 60.0)

    dm.maybe_schedule_deadline_dlq_sweep("tenant-a", None)
    dm.maybe_schedule_deadline_dlq_sweep("tenant-a", None)
    await asyncio.sleep(0)

    assert calls == ["tenant-a"]
    dm._reset_deadline_sweep_state_for_tests()


@pytest.mark.asyncio
async def test_run_deadline_dlq_sweep_moves_expired_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    dm._reset_deadline_sweep_state_for_tests()
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    job = MagicMock()
    job.tenant_id = "tenant-a"
    job.job_id = "job-1"
    job.deadline_at = now - datetime.timedelta(seconds=1)

    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [job]
    result.scalars.return_value = scalars

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    class _SessionContext:
        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            del exc_type, exc, tb
            return False

    factory = MagicMock(return_value=_SessionContext())

    import backend.db as db_module

    monkeypatch.setattr(db_module, "_async_session_factory", factory)
    monkeypatch.setattr(dm, "set_tenant_context", AsyncMock(return_value=None))
    monkeypatch.setattr(dm, "move_to_dead_letter_queue", AsyncMock(return_value=None))
    monkeypatch.setattr(dm, "_append_log", AsyncMock(return_value=None))
    monkeypatch.setattr(
        dm,
        "get_policy_store",
        lambda: SimpleNamespace(active=SimpleNamespace(dispatch=SimpleNamespace(dlq_scan_limit=10))),
    )
    monkeypatch.setattr(dm, "_utcnow", lambda: now)

    moved = await dm._run_deadline_dlq_sweep("tenant-a", None)

    assert moved == 1
    assert job.status == "failed"
    assert job.failure_category == "deadline_expired"
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    dm.move_to_dead_letter_queue.assert_awaited_once()  # type: ignore[attr-defined]
    dm._append_log.assert_awaited_once()  # type: ignore[attr-defined]
