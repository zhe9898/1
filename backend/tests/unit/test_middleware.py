from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse
from starlette.responses import Response


class TestRequestIDMiddleware:
    @pytest.mark.asyncio
    async def test_generates_request_id_when_missing(self) -> None:
        from backend.middleware import RequestIDMiddleware

        middleware = RequestIDMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {}
        request.state = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}
        call_next = AsyncMock(return_value=mock_response)

        await middleware.dispatch(request, call_next)

        assert hasattr(request.state, "request_id")
        assert request.state.request_id is not None
        assert len(request.state.request_id) > 0
        assert mock_response.headers["X-Request-ID"] == request.state.request_id

    @pytest.mark.asyncio
    async def test_reuses_upstream_request_id(self) -> None:
        from backend.middleware import RequestIDMiddleware

        middleware = RequestIDMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {"X-Request-ID": "upstream-rid-001"}
        request.state = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}
        call_next = AsyncMock(return_value=mock_response)

        await middleware.dispatch(request, call_next)

        assert request.state.request_id == "upstream-rid-001"
        assert mock_response.headers["X-Request-ID"] == "upstream-rid-001"

    @pytest.mark.asyncio
    async def test_supports_x_trace_id_fallback(self) -> None:
        from backend.middleware import RequestIDMiddleware

        middleware = RequestIDMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {"X-Trace-Id": "trace-abc"}
        request.state = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}
        call_next = AsyncMock(return_value=mock_response)

        await middleware.dispatch(request, call_next)

        assert request.state.request_id == "trace-abc"

    @pytest.mark.asyncio
    async def test_replaces_invalid_upstream_request_id(self) -> None:
        from backend.middleware import RequestIDMiddleware

        middleware = RequestIDMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {"X-Request-ID": "bad\nid"}
        request.state = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}
        call_next = AsyncMock(return_value=mock_response)

        await middleware.dispatch(request, call_next)

        assert request.state.request_id != "bad\nid"
        assert len(request.state.request_id) == 32
        assert mock_response.headers["X-Request-ID"] == request.state.request_id

    @pytest.mark.asyncio
    async def test_replaces_overlong_upstream_request_id(self) -> None:
        from backend.middleware import RequestIDMiddleware

        middleware = RequestIDMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {"X-Request-ID": "x" * 129}
        request.state = MagicMock()

        mock_response = MagicMock()
        mock_response.headers = {}
        call_next = AsyncMock(return_value=mock_response)

        await middleware.dispatch(request, call_next)

        assert request.state.request_id != ("x" * 129)
        assert len(request.state.request_id) == 32


class TestLimitRequestBody:
    @pytest.mark.asyncio
    async def test_rejects_oversized_post(self) -> None:
        from backend.middleware import limit_request_body

        request = MagicMock()
        request.method = "POST"
        request.headers = {"content-length": "999999999"}
        request.state = MagicMock()

        call_next = AsyncMock()
        response = await limit_request_body(request, call_next)

        assert response.status_code == 413
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_allows_small_post(self) -> None:
        from backend.middleware import limit_request_body

        request = MagicMock()
        request.method = "POST"
        request.headers = {"content-length": "1024"}
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)
        response = await limit_request_body(request, call_next)

        assert response is expected_response
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_bypasses_limit(self) -> None:
        from backend.middleware import limit_request_body

        request = MagicMock()
        request.method = "GET"
        request.headers = {"content-length": "999999999"}
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)
        response = await limit_request_body(request, call_next)

        assert response is expected_response

    @pytest.mark.asyncio
    async def test_invalid_content_length_passes(self) -> None:
        from backend.middleware import limit_request_body

        request = MagicMock()
        request.method = "POST"
        request.headers = {"content-length": "not-a-number"}
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)
        response = await limit_request_body(request, call_next)

        assert response is expected_response


class TestGlobalReadOnlyLock:
    @pytest.mark.asyncio
    async def test_get_passes_through(self) -> None:
        from backend.middleware import global_readonly_lock

        request = MagicMock()
        request.method = "GET"
        request.url = MagicMock()
        request.url.path = "/api/v1/health"

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        with patch("backend.middleware.service_readiness", {}):
            response = await global_readonly_lock(request, call_next)

        assert response is expected_response

    @pytest.mark.asyncio
    async def test_post_blocked_when_ups_low_battery_lru(self) -> None:
        from backend.capabilities import CapabilityItem
        from backend.middleware import global_readonly_lock

        ups_cap = CapabilityItem(status="low-battery-shutdown", enabled=False)  # type: ignore[call-arg]
        matrix = {"ups": ups_cap}

        request = MagicMock()
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/api/v1/data"
        request.state = MagicMock()

        call_next = AsyncMock()

        with patch("backend.middleware.get_lru_matrix", return_value=matrix):
            with patch("backend.middleware.service_readiness", {}):
                response = await global_readonly_lock(request, call_next)

        assert response.status_code == 503
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_post_passes_when_ups_online(self) -> None:
        from backend.capabilities import CapabilityItem
        from backend.middleware import global_readonly_lock

        ups_cap = CapabilityItem(status="online", enabled=True)  # type: ignore[call-arg]
        matrix = {"ups": ups_cap}

        request = MagicMock()
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/api/v1/data"
        request.state = MagicMock()

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        with patch("backend.middleware.get_lru_matrix", return_value=matrix):
            with patch("backend.middleware.service_readiness", {}):
                response = await global_readonly_lock(request, call_next)

        assert response is expected_response

    @pytest.mark.asyncio
    async def test_service_not_ready_returns_503(self) -> None:
        from backend.middleware import global_readonly_lock

        request = MagicMock()
        request.method = "GET"
        request.url = MagicMock()
        request.url.path = "/api/v1/jellyfin/status"
        request.state = MagicMock()

        call_next = AsyncMock()

        with patch("backend.middleware.get_lru_matrix", return_value=None):
            with patch("backend.middleware.service_readiness", {"jellyfin": False}):
                response = await global_readonly_lock(request, call_next)

        assert response.status_code == 503
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pipeline_timeout_falls_back_to_request_handler(self) -> None:
        from backend.middleware import global_readonly_lock

        request = MagicMock()
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/api/v1/data"
        request.state = MagicMock()
        request.app.state.redis = MagicMock()
        request.app.state.redis.kv = MagicMock(get_many=AsyncMock(side_effect=asyncio.TimeoutError()))

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        with patch("backend.middleware.get_lru_matrix", return_value=None):
            with patch("backend.middleware.service_readiness", {}):
                response = await global_readonly_lock(request, call_next)

        assert response is expected_response

    @pytest.mark.asyncio
    async def test_pipeline_partial_results_do_not_break_request(self) -> None:
        from backend.middleware import global_readonly_lock

        request = MagicMock()
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/api/v1/data"
        request.state = MagicMock()
        request.app.state.redis = MagicMock()
        request.app.state.redis.kv = MagicMock(get_many=AsyncMock(return_value=["ONLINE"]))

        expected_response = MagicMock()
        call_next = AsyncMock(return_value=expected_response)

        with patch("backend.middleware.get_lru_matrix", return_value=None):
            with patch("backend.middleware.service_readiness", {}):
                response = await global_readonly_lock(request, call_next)

        assert response is expected_response


class TestSuccessEnvelope:
    @pytest.mark.asyncio
    async def test_wraps_json_success(self) -> None:
        from backend.api.main import success_envelope

        request = MagicMock()
        request.url = MagicMock()
        request.url.path = "/api/v1/test"
        request.state = MagicMock()
        request.state.request_id = "rid-001"

        inner_body = json.dumps({"items": [1, 2, 3]}).encode("utf-8")

        async def body_iter() -> object:
            yield inner_body

        inner = Response(content=inner_body, status_code=200, media_type="application/json")
        inner.body_iterator = body_iter()  # type: ignore[attr-defined]

        async def mock_call_next(_req: object) -> Response:
            return inner

        response = await success_envelope(request, mock_call_next)
        body = json.loads(response.body.decode("utf-8"))

        assert body["code"] == "ZEN-OK-0"
        assert body["message"] == "ok"
        assert body["data"] == {"items": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_skips_error_responses(self) -> None:
        from backend.api.main import success_envelope

        request = MagicMock()
        request.url = MagicMock()
        request.url.path = "/api/v1/test"
        request.state = MagicMock()

        error_resp = JSONResponse(content={"error": "bad"}, status_code=400)

        async def mock_call_next(_req: object) -> Response:
            return error_resp

        response = await success_envelope(request, mock_call_next)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_skips_non_json(self) -> None:
        from backend.api.main import success_envelope

        request = MagicMock()
        request.url = MagicMock()
        request.url.path = "/api/v1/test"
        request.state = MagicMock()

        html_resp = Response(content="<html></html>", media_type="text/html")

        async def mock_call_next(_req: object) -> Response:
            return html_resp

        response = await success_envelope(request, mock_call_next)
        assert response.media_type == "text/html"

    @pytest.mark.asyncio
    async def test_skips_already_enveloped(self) -> None:
        from backend.api.main import success_envelope

        request = MagicMock()
        request.url = MagicMock()
        request.url.path = "/api/v1/test"
        request.state = MagicMock()

        enveloped_body = json.dumps({"code": "ZEN-OK-0", "message": "ok", "data": {"x": 1}}).encode("utf-8")

        async def body_iter() -> object:
            yield enveloped_body

        enveloped = Response(content=enveloped_body, status_code=200, media_type="application/json")
        enveloped.body_iterator = body_iter()  # type: ignore[attr-defined]

        async def mock_call_next(_req: object) -> Response:
            return enveloped

        response = await success_envelope(request, mock_call_next)
        body = json.loads(response.body.decode("utf-8"))
        assert body.get("code") == "ZEN-OK-0"
        assert body["data"] == {"x": 1}


class TestAddRequestIdDeprecated:
    def test_raises_runtime_error(self) -> None:
        from backend.middleware import add_request_id

        with pytest.raises(RuntimeError, match="deprecated"):
            add_request_id()
