"""ZEN70 Energy Monitoring — metric whitelist, Prometheus queries, Pydantic contracts."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

# ── Prometheus query whitelist ──────────────────────────────────────
METRIC_QUERIES: dict[str, str] = {
    "cpu_usage": 'avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100',
    "memory_usage": "(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100",
    "disk_usage": '(1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100',
    "gpu_temp": "nvidia_smi_temperature_gpu",
    "gpu_util": "nvidia_smi_utilization_gpu_ratio * 100",
    "gpu_memory": "nvidia_smi_utilization_memory_ratio * 100",
    "network_in": "rate(node_network_receive_bytes_total[5m])",
    "network_out": "rate(node_network_transmit_bytes_total[5m])",
    "docker_containers": "engine_daemon_container_states_containers",
}

METRIC_META: dict[str, dict[str, str]] = {
    "cpu_usage": {"label": "CPU 使用率", "unit": "%"},
    "memory_usage": {"label": "内存使用率", "unit": "%"},
    "disk_usage": {"label": "磁盘使用率", "unit": "%"},
    "gpu_temp": {"label": "GPU 温度", "unit": "°C"},
    "gpu_util": {"label": "GPU 利用率", "unit": "%"},
    "gpu_memory": {"label": "GPU 显存", "unit": "%"},
    "network_in": {"label": "入站流量", "unit": "B/s"},
    "network_out": {"label": "出站流量", "unit": "B/s"},
    "docker_containers": {"label": "容器数", "unit": "个"},
}


# ── Pydantic models ────────────────────────────────────────────────
class MetricDataPoint(BaseModel):
    timestamp: float
    value: float


class MetricResponse(BaseModel):
    metric: str
    label: str
    unit: str
    data: list[MetricDataPoint] = Field(default_factory=list)


class EnergyOverviewResponse(BaseModel):
    cpu_usage: float | None = None
    memory_usage: float | None = None
    disk_usage: float | None = None
    gpu_temp: float | None = None
    gpu_util: float | None = None
    gpu_memory: float | None = None
    network_in: float | None = None
    network_out: float | None = None
    docker_containers: float | None = None


class MetricListResponse(BaseModel):
    metrics: list[dict[str, Any]] = Field(default_factory=list)


# ── Query endpoint ─────────────────────────────────────────────────
async def query_history(
    metric: str,
    range_str: str = "1h",
    step: str = "60s",
    current_user: dict[str, Any] | None = None,
) -> MetricResponse:
    """Query Prometheus for metric history."""
    if metric not in METRIC_QUERIES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "ZEN-ENERGY-4000",
                "message": f"Unknown metric: {metric}",
                "recovery_hint": f"Valid metrics: {', '.join(sorted(METRIC_QUERIES))}",
            },
        )
    meta = METRIC_META[metric]
    return MetricResponse(metric=metric, label=meta["label"], unit=meta["unit"], data=[])
