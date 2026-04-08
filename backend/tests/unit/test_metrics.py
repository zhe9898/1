"""Unit tests for Prometheus metrics middleware."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestMetricsMiddleware:
    @pytest.mark.asyncio
    async def test_records_request_duration(self) -> None:
        from backend.platform.telemetry.http_metrics import metrics_middleware

        request = MagicMock()
        request.url.path = "/api/v1/data"
        request.method = "GET"
        request.scope = {}

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        result = await metrics_middleware(request, call_next)
        assert result is response
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_metrics_endpoint(self) -> None:
        from backend.platform.telemetry.http_metrics import metrics_middleware

        request = MagicMock()
        request.url.path = "/metrics"
        request.method = "GET"
        request.scope = {}

        response = MagicMock()
        call_next = AsyncMock(return_value=response)

        result = await metrics_middleware(request, call_next)
        assert result is response

    @pytest.mark.asyncio
    async def test_skips_health_endpoint(self) -> None:
        from backend.platform.telemetry.http_metrics import metrics_middleware

        request = MagicMock()
        request.url.path = "/api/v1/health"
        request.method = "GET"
        request.scope = {}

        response = MagicMock()
        call_next = AsyncMock(return_value=response)

        result = await metrics_middleware(request, call_next)
        assert result is response

    @pytest.mark.asyncio
    async def test_exception_records_500(self) -> None:
        from backend.platform.telemetry.http_metrics import metrics_middleware

        request = MagicMock()
        request.url.path = "/api/v1/crash"
        request.method = "POST"
        request.scope = {}

        call_next = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await metrics_middleware(request, call_next)

    def test_normalize_endpoint_uses_route_template(self) -> None:
        from backend.platform.telemetry.http_metrics import _normalize_endpoint_label

        request = MagicMock()
        request.scope = {"route": SimpleNamespace(path="/api/v1/jobs/{id}")}

        assert _normalize_endpoint_label(request, "/api/v1/jobs/123456") == "/api/v1/jobs/{id}"


class TestMetricsDefinitions:
    def test_counter_exists(self) -> None:
        from backend.platform.telemetry.http_metrics import API_REQUESTS_TOTAL

        assert API_REQUESTS_TOTAL is not None

    def test_histogram_exists(self) -> None:
        from backend.platform.telemetry.http_metrics import API_REQUEST_DURATION

        assert API_REQUEST_DURATION is not None

    def test_gauge_exists(self) -> None:
        from backend.platform.telemetry.http_metrics import ACTIVE_CONNECTIONS

        assert ACTIVE_CONNECTIONS is not None
