"""
api/energy.py 单元测试 — 指标查询契约、白名单、Prometheus 降级。

高 ROI：确认指标白名单拒绝未知指标，Pydantic 模型完整。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


class TestMetricQueries:
    """METRIC_QUERIES 白名单验证。"""

    def test_all_standard_metrics_present(self) -> None:
        from backend.api.energy import METRIC_QUERIES

        expected = {"cpu_usage", "memory_usage", "disk_usage", "gpu_temp", "gpu_util", "gpu_memory", "network_in", "network_out", "docker_containers"}
        assert expected == set(METRIC_QUERIES.keys())

    def test_no_sql_injection_in_queries(self) -> None:
        """Prometheus 查询中不应包含 SQL 注入风险字符。"""
        from backend.api.energy import METRIC_QUERIES

        for key, query in METRIC_QUERIES.items():
            assert "DROP" not in query.upper(), f"{key} 查询包含可疑内容"
            assert "DELETE" not in query.upper(), f"{key} 查询包含可疑内容"


class TestMetricMeta:
    """METRIC_META 元数据一致性。"""

    def test_meta_covers_all_queries(self) -> None:
        from backend.api.energy import METRIC_META, METRIC_QUERIES

        for key in METRIC_QUERIES:
            assert key in METRIC_META, f"指标 {key} 在 METRIC_META 中缺失"
            assert "label" in METRIC_META[key]
            assert "unit" in METRIC_META[key]


class TestHistoryEndpointValidation:
    """query_history 输入验证。"""

    @pytest.mark.anyio
    async def test_unknown_metric_returns_400(self) -> None:
        from backend.api.energy import query_history

        with pytest.raises(HTTPException) as exc_info:
            await query_history(metric="nonexistent_metric", range_str="1h", step="60s", current_user={"sub": "test"})
        assert exc_info.value.status_code == 400
        assert "ZEN-ENERGY-4000" in str(exc_info.value.detail)


class TestPydanticModels:
    """能耗 Pydantic 模型完整性。"""

    def test_metric_data_point(self) -> None:
        from backend.api.energy import MetricDataPoint

        dp = MetricDataPoint(timestamp=1700000000.0, value=42.5)
        assert dp.timestamp == 1700000000.0
        assert dp.value == 42.5

    def test_metric_response(self) -> None:
        from backend.api.energy import MetricDataPoint, MetricResponse

        resp = MetricResponse(
            metric="cpu_usage",
            label="CPU 使用率",
            unit="%",
            data=[MetricDataPoint(timestamp=1700000000.0, value=50.0)],
        )
        assert resp.metric == "cpu_usage"
        assert len(resp.data) == 1

    def test_energy_overview_optional_fields(self) -> None:
        from backend.api.energy import EnergyOverviewResponse

        overview = EnergyOverviewResponse()
        assert overview.cpu_usage is None
        assert overview.gpu_temp is None

    def test_metric_list_response(self) -> None:
        from backend.api.energy import MetricListResponse

        resp = MetricListResponse(metrics=[{"id": "cpu_usage", "label": "CPU", "unit": "%"}])
        assert len(resp.metrics) == 1
