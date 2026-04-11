from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.workers.control_plane_worker import _worker_factories, run


def test_worker_factories_support_all_and_named_modes() -> None:
    assert set(_worker_factories("all")) == {"attempt-expiration", "bitrot", "health-probe", "data-retention"}
    assert set(_worker_factories("attempt-expiration")) == {"attempt-expiration"}
    assert set(_worker_factories("bitrot")) == {"bitrot"}
    assert set(_worker_factories("health-probe")) == {"health-probe"}
    assert set(_worker_factories("data-retention")) == {"data-retention"}


@pytest.mark.asyncio
async def test_control_plane_worker_runs_out_of_process_and_stops_on_signal() -> None:
    handlers: dict[signal.Signals, object] = {}
    started = {
        "attempt-expiration": asyncio.Event(),
        "bitrot": asyncio.Event(),
        "health-probe": asyncio.Event(),
        "data-retention": asyncio.Event(),
    }
    cancelled = {
        "attempt-expiration": asyncio.Event(),
        "bitrot": asyncio.Event(),
        "health-probe": asyncio.Event(),
        "data-retention": asyncio.Event(),
    }

    async def fake_attempt_expiration_worker() -> None:
        started["attempt-expiration"].set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["attempt-expiration"].set()
            raise

    async def fake_bitrot_worker() -> None:
        started["bitrot"].set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["bitrot"].set()
            raise

    async def fake_health_probe_worker(app_redis: object = None) -> None:
        assert app_redis is not None
        started["health-probe"].set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["health-probe"].set()
            raise

    async def fake_data_retention_worker() -> None:
        started["data-retention"].set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled["data-retention"].set()
            raise

    def fake_getsignal(sig: signal.Signals) -> object:
        return handlers.get(sig, MagicMock())

    def fake_signal(sig: signal.Signals, handler: object) -> object:
        previous = handlers.get(sig, MagicMock())
        handlers[sig] = handler
        return previous

    redis_client = AsyncMock()
    event_bus = AsyncMock()

    with (
        patch("backend.workers.control_plane_worker.connect_redis_with_retry", new=AsyncMock(return_value=redis_client)),
        patch("backend.workers.control_plane_worker.connect_event_bus_with_retry", new=AsyncMock(return_value=event_bus)),
        patch("backend.workers.control_plane_worker.attempt_expiration_worker", new=fake_attempt_expiration_worker),
        patch("backend.workers.control_plane_worker.bitrot_worker", new=fake_bitrot_worker),
        patch("backend.workers.control_plane_worker.health_probe_worker", new=fake_health_probe_worker),
        patch("backend.workers.control_plane_worker.data_retention_worker", new=fake_data_retention_worker),
        patch("backend.workers.control_plane_worker.signal.getsignal", side_effect=fake_getsignal),
        patch("backend.workers.control_plane_worker.signal.signal", side_effect=fake_signal),
    ):
        task = asyncio.create_task(run("all"))
        await asyncio.wait_for(started["attempt-expiration"].wait(), timeout=1)
        await asyncio.wait_for(started["bitrot"].wait(), timeout=1)
        await asyncio.wait_for(started["health-probe"].wait(), timeout=1)
        await asyncio.wait_for(started["data-retention"].wait(), timeout=1)
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)  # type: ignore[misc]
        await asyncio.wait_for(task, timeout=1)

    redis_client.close.assert_awaited_once()
    event_bus.close.assert_awaited_once()
    assert cancelled["attempt-expiration"].is_set()
    assert cancelled["bitrot"].is_set()
    assert cancelled["health-probe"].is_set()
    assert cancelled["data-retention"].is_set()
