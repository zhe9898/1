"""
能力矩阵契约测试：保证 /api/v1/capabilities 响应形状稳定，前端协议驱动渲染不静默断裂。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.capabilities import (
    ALL_OFF_MATRIX,
    CapabilityItem,
    get_capabilities_matrix,
)


@pytest.mark.asyncio
async def test_all_off_matrix_shape() -> None:
    """冷启动矩阵：每项为 CapabilityItem，含 status / enabled / reason。"""
    assert isinstance(ALL_OFF_MATRIX, dict)
    assert "ups" in ALL_OFF_MATRIX
    assert "network" in ALL_OFF_MATRIX
    assert "gpu" in ALL_OFF_MATRIX
    for key, item in ALL_OFF_MATRIX.items():
        assert isinstance(item, CapabilityItem)
        assert hasattr(item, "status")
        assert hasattr(item, "enabled")
        assert item.status in ("online", "offline", "pending_maintenance", "unknown")
        assert isinstance(item.enabled, bool)


@pytest.mark.asyncio
async def test_get_capabilities_matrix_returns_dict_of_capability_item() -> None:
    """get_capabilities_matrix 返回 Dict[str, CapabilityItem]，每项含 status/enabled。"""
    req = MagicMock()
    req.app.state = MagicMock(spec=[])  # 确保 state 无 redis 属性
    with patch("backend.capabilities._get_redis_from_app", return_value=None):
        matrix = await get_capabilities_matrix(req)
    assert isinstance(matrix, dict)
    for key, item in matrix.items():
        assert isinstance(item, CapabilityItem), f"{key!r} is not CapabilityItem"
        assert hasattr(item, "status") and hasattr(item, "enabled")
        assert isinstance(item.status, str)
        assert isinstance(item.enabled, bool)
