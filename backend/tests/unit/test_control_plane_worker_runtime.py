from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.sentinel.control_plane_supervisor import _child_commands
from backend.workers.control_plane_worker import _worker_factories, run


def test_worker_factories_support_all_and_named_modes() -> None:
    assert set(_worker_factories("all")) == {"bitrot", "health-probe"}
    assert set(_worker_factories("bitrot")) == {"bitrot"}
    assert set(_worker_factories("health-probe")) == {"health-probe"}


def test_control_plane_supervisor_child_commands() -> None:
    commands = _child_commands(Path("E:/3.4"))

    assert "topology-sentinel" in commands
    assert "control-worker" in commands
    assert "routing-operator" in commands
    assert commands["topology-sentinel"][-1].endswith("backend\\sentinel\\topology_sentinel.py") or commands["topology-sentinel"][-1].endswith(
        "backend/sentinel/topology_sentinel.py"
    )
    assert commands["control-worker"][-3:] == ["backend.workers.control_plane_worker", "--worker", "all"]
    assert commands["routing-operator"][-1].endswith("backend\\sentinel\\routing_operator.py") or commands["routing-operator"][-1].endswith(
        "backend/sentinel/routing_operator.py"
    )


@pytest.mark.asyncio
async def test_control_plane_worker_runs_out_of_process_and_stops_on_signal() -> None:
    handlers: dict[signal.Signals, object] = {}
    started = {"bitrot": asyncio.Event(), "health-probe": asyncio.Event()}
    cancelled = {"bitrot": asyncio.Event(), "health-probe": asyncio.Event()}

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

    def fake_getsignal(sig: signal.Signals) -> object:
        return handlers.get(sig, MagicMock())

    def fake_signal(sig: signal.Signals, handler: object) -> object:
        previous = handlers.get(sig, MagicMock())
        handlers[sig] = handler
        return previous

    redis_client = AsyncMock()

    with (
        patch("backend.workers.control_plane_worker.connect_redis_with_retry", new=AsyncMock(return_value=redis_client)),
        patch("backend.workers.control_plane_worker.bitrot_worker", new=fake_bitrot_worker),
        patch("backend.workers.control_plane_worker.health_probe_worker", new=fake_health_probe_worker),
        patch("backend.workers.control_plane_worker.signal.getsignal", side_effect=fake_getsignal),
        patch("backend.workers.control_plane_worker.signal.signal", side_effect=fake_signal),
    ):
        task = asyncio.create_task(run("all"))
        await asyncio.wait_for(started["bitrot"].wait(), timeout=1)
        await asyncio.wait_for(started["health-probe"].wait(), timeout=1)
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)  # type: ignore[misc]
        await asyncio.wait_for(task, timeout=1)

    redis_client.close.assert_awaited_once()
    assert cancelled["bitrot"].is_set()
    assert cancelled["health-probe"].is_set()
