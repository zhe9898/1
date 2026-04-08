from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("zen70.telemetry")

_OTEL_ENABLED = False


def is_otel_enabled() -> bool:
    """Return whether OTEL tracing has been activated for the process."""
    return _OTEL_ENABLED


def init_telemetry(app: FastAPI) -> None:
    """Conditionally initialize OpenTelemetry when OTLP is configured."""
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

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="health,metrics",
    )

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.info("OTEL httpx instrumentation enabled (kernel outbound propagation)")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-httpx not installed; skipping outbound propagation")

    _OTEL_ENABLED = True
    logger.info("OpenTelemetry tracing enabled -> %s (service=%s)", endpoint, service_name)


def shutdown_telemetry() -> None:
    """Gracefully flush and shut down the active tracer provider."""
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
    except Exception:
        logger.debug("OTEL shutdown error (non-fatal)", exc_info=True)
