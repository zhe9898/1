from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import Counter, Gauge, Histogram

API_REQUESTS_TOTAL = Counter(
    "zen70_api_requests_total",
    "Total HTTP requests handled by the FastAPI application",
    ["method", "endpoint", "status"],
)

API_REQUEST_DURATION = Histogram(
    "zen70_api_request_duration_seconds",
    "HTTP request duration in seconds (Target P99 <= 500ms)",
    ["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

ACTIVE_CONNECTIONS = Gauge(
    "zen70_sse_active_connections",
    "Number of active HTTP/SSE connections currently handled by the server",
)


def _normalize_endpoint_label(request: Request, raw_path: str) -> str:
    scope: Any = getattr(request, "scope", None)
    if isinstance(scope, dict):
        route = scope.get("route")
        route_path = getattr(route, "path", None)
        if isinstance(route_path, str) and route_path.strip():
            return route_path
    segments = [part for part in raw_path.split("/") if part]
    normalized: list[str] = []
    for part in segments:
        token = part.strip()
        if not token:
            continue
        if token.isdigit() or len(token) >= 16:
            normalized.append(":id")
            continue
        normalized.append(token)
    return "/" + "/".join(normalized)


async def metrics_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Record request latency, status, and active connection counts."""
    path = request.url.path
    if path == "/metrics" or path == "/api/v1/health":
        return await call_next(request)

    method = request.method
    endpoint_label = _normalize_endpoint_label(request, path)

    ACTIVE_CONNECTIONS.inc()
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
        status = str(response.status_code)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        status = "500"
        raise exc
    finally:
        ACTIVE_CONNECTIONS.dec()
        duration = time.perf_counter() - start_time
        API_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint_label, status=status).inc()
        API_REQUEST_DURATION.labels(method=method, endpoint=endpoint_label).observe(duration)

    return response
