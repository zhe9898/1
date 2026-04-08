"""Tests for governance_facade extended proxy methods."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.governance_facade import GovernanceFacade


def _now() -> datetime.datetime:
    return datetime.datetime(2025, 1, 15, 12, 0, 0)


@pytest.fixture
def facade() -> GovernanceFacade:
    return GovernanceFacade()


class TestFailureControlPlaneProxies:
    @pytest.mark.asyncio
    async def test_is_node_quarantined(self, facade: GovernanceFacade) -> None:
        with patch("backend.core.failure_control_plane.get_failure_control_plane") as mock_get:
            fcp = MagicMock()
            fcp.is_node_quarantined = AsyncMock(return_value=True)
            mock_get.return_value = fcp
            result = await facade.is_node_quarantined("node-1", now=_now())
        assert result is True
        fcp.is_node_quarantined.assert_awaited_once_with("node-1", now=_now())

    @pytest.mark.asyncio
    async def test_is_connector_cooling(self, facade: GovernanceFacade) -> None:
        with patch("backend.core.failure_control_plane.get_failure_control_plane") as mock_get:
            fcp = MagicMock()
            fcp.is_connector_cooling = AsyncMock(return_value=False)
            mock_get.return_value = fcp
            result = await facade.is_connector_cooling("conn-1", now=_now())
        assert result is False

    @pytest.mark.asyncio
    async def test_get_kind_circuit_state(self, facade: GovernanceFacade) -> None:
        with patch("backend.core.failure_control_plane.get_failure_control_plane") as mock_get:
            fcp = MagicMock()
            fcp.get_kind_circuit_state = AsyncMock(return_value="closed")
            mock_get.return_value = fcp
            result = await facade.get_kind_circuit_state("shell.exec", now=_now())
        assert result == "closed"

    @pytest.mark.asyncio
    async def test_is_in_burst(self, facade: GovernanceFacade) -> None:
        with patch("backend.core.failure_control_plane.get_failure_control_plane") as mock_get:
            fcp = MagicMock()
            fcp.is_in_burst = AsyncMock(return_value=False)
            mock_get.return_value = fcp
            result = await facade.is_in_burst(now=_now())
        assert result is False

    @pytest.mark.asyncio
    async def test_fcp_snapshot(self, facade: GovernanceFacade) -> None:
        with patch("backend.core.failure_control_plane.get_failure_control_plane") as mock_get:
            snap = {"quarantined": [], "cooling": [], "burst": False}
            fcp = MagicMock()
            fcp.snapshot = AsyncMock(return_value=snap)
            mock_get.return_value = fcp
            result = await facade.fcp_snapshot(now=_now())
        assert result == snap


class TestFairSchedulerProxies:
    def test_get_tenant_quota(self, facade: GovernanceFacade) -> None:
        with patch("backend.kernel.scheduling.queue_stratification.get_fair_scheduler") as mock_get:
            fs = MagicMock()
            fs.get_quota.return_value = {"max_concurrent": 10, "weight": 1.0}
            mock_get.return_value = fs
            result = facade.get_tenant_quota("tenant-1")
        assert result["max_concurrent"] == 10

    def test_apply_fair_share(self, facade: GovernanceFacade) -> None:
        with patch("backend.kernel.scheduling.queue_stratification.get_fair_scheduler") as mock_get:
            fs = MagicMock()
            fs.apply_fair_share.return_value = ["j1", "j2"]
            mock_get.return_value = fs
            result = facade.apply_fair_share(["j1", "j2", "j3"])
        assert result == ["j1", "j2"]

    def test_invalidate_cache(self, facade: GovernanceFacade) -> None:
        with patch("backend.kernel.scheduling.queue_stratification.get_fair_scheduler") as mock_get:
            fs = MagicMock()
            mock_get.return_value = fs
            facade.invalidate_fair_share_cache()
        fs.invalidate_cache.assert_called_once()


class TestPlacementSolverProxy:
    def test_run_placement_solver(self, facade: GovernanceFacade) -> None:
        with patch("backend.kernel.scheduling.job_scheduler.get_placement_solver") as mock_get:
            solver = MagicMock()
            solver.solve.return_value = {"j1": "n1"}
            mock_get.return_value = solver
            result = facade.run_placement_solver(
                jobs=["j1"],
                nodes=["n1"],
                now=_now(),
                accepted_kinds={"shell.exec"},
            )
        assert result == {"j1": "n1"}
        solver.solve.assert_called_once()


class TestExecutorRegistryProxies:
    def test_validate_node_executor(self, facade: GovernanceFacade) -> None:
        with patch("backend.kernel.topology.executor_registry.get_executor_registry") as mock_get:
            reg = MagicMock()
            reg.validate_node_executor.return_value = []
            mock_get.return_value = reg
            result = facade.validate_node_executor("docker")
        assert result == []

    def test_get_executor_contract(self, facade: GovernanceFacade) -> None:
        with patch("backend.kernel.topology.executor_registry.get_executor_registry") as mock_get:
            contract = MagicMock()
            contract.kind = "docker"
            reg = MagicMock()
            reg.get.return_value = contract
            mock_get.return_value = reg
            result = facade.get_executor_contract("docker")
        assert result.kind == "docker"


class TestDispatchLifecycleProxy:
    def test_get_dispatch_pipeline(self, facade: GovernanceFacade) -> None:
        with patch("backend.kernel.execution.dispatch_lifecycle.get_dispatch_pipeline") as mock_get:
            pipeline = MagicMock()
            mock_get.return_value = pipeline
            result = facade.get_dispatch_pipeline()
        assert result is pipeline
