"""
ZEN70 Kernel Telemetry — OpenTelemetry 追踪初始化。

内核级可观测性基础设施：
- 条件初始化：仅当 OTEL_EXPORTER_OTLP_ENDPOINT 配置时激活
- 桥接现有 X-Request-ID 到 W3C traceparent
- FastAPI 自动 Span + httpx 出站传播
- 导出至 OTLP 端点（Grafana Tempo / Jaeger / 等）

控制面层关注，不涉及业务 Pack 逻辑。
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("zen70.telemetry")

_OTEL_ENABLED = False


def is_otel_enabled() -> bool:
    """运行时查询 OTEL 是否已激活。"""
    return _OTEL_ENABLED


def init_telemetry(app: FastAPI) -> None:
    """条件初始化 OpenTelemetry，仅当 OTEL_EXPORTER_OTLP_ENDPOINT 存在时激活。

    幂等：多次调用安全，仅首次生效。
    """
    global _OTEL_ENABLED  # noqa: PLW0603

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set; telemetry disabled")
        return

    if _OTEL_ENABLED:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "OpenTelemetry packages not installed; telemetry disabled. "
            "Install: pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-instrumentation-fastapi opentelemetry-exporter-otlp-proto-http"
        )
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "zen70-gateway-kernel")
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # FastAPI 自动 instrumentation — 为每个请求创建 Span
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,metrics",
    )

    # httpx 出站传播（ai_router 反向代理等内核出站调用）
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.info("OTEL httpx instrumentation enabled (kernel outbound propagation)")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-httpx not installed; skipping outbound propagation")

    _OTEL_ENABLED = True
    logger.info("OpenTelemetry tracing enabled → %s (service=%s)", endpoint, service_name)


def shutdown_telemetry() -> None:
    """优雅关闭 TracerProvider，flush 剩余 Span。"""
    global _OTEL_ENABLED  # noqa: PLW0603
    if not _OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
        _OTEL_ENABLED = False
        logger.info("OpenTelemetry tracing shut down")
    except Exception:  # noqa: BLE001
        logger.debug("OTEL shutdown error (non-fatal)", exc_info=True)
