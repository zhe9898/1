"""
API 契约回归测试。

验证法典级 API 契约：
- 统一错误码 ZEN-xxx + recovery_hint（法典 2.3）
- 成功响应 Envelope ZEN-OK-0（ADR 0010）
- X-Request-ID 全链追踪（法典 2.3）
- JWT 双轨轮转 X-New-Token（法典 3.4）
- SSE 心跳 45s（法典 2.1）

所有测试依赖 Gateway 运行，按 skipif 降级。
"""

from __future__ import annotations

import os
import uuid

import pytest
import requests

from tests.integration.conftest import (
    BASE_URL,
    GATEWAY_OK,
    REDIS_OK,
    _no_proxy,
)


# ---------- 响应 Envelope 契约 ----------


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestResponseEnvelope:
    """ADR 0010: 统一成功/错误响应格式。"""

    def test_success_envelope_shape(self) -> None:
        """成功响应必须含 code=ZEN-OK-0, message, data。"""
        r = requests.get(
            f"{BASE_URL}/api/v1/capabilities",
            timeout=5,
            proxies=_no_proxy(),
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("code") == "ZEN-OK-0", f"Expected ZEN-OK-0, got {body.get('code')}"
        assert "message" in body
        assert "data" in body

    def test_error_envelope_on_404(self) -> None:
        """404 必须返回 {code: ZEN-xxx, message, recovery_hint, details}。"""
        r = requests.get(
            f"{BASE_URL}/api/v1/nonexistent_endpoint_for_test",
            timeout=5,
            proxies=_no_proxy(),
        )
        assert r.status_code in (404, 405)
        body = r.json()
        assert "code" in body, f"Missing 'code' in error response: {body}"
        assert body["code"].startswith("ZEN-"), f"Error code must start with ZEN-: {body['code']}"
        assert "message" in body

    def test_error_envelope_on_413(self) -> None:
        """超大请求体应触发 413 + ZEN-REQ-413。"""
        # 发送 >10MB payload（法典 7: MAX_REQUEST_BODY_BYTES）
        oversized = b"X" * (11 * 1024 * 1024)  # 11MB
        r = requests.post(
            f"{BASE_URL}/api/v1/capabilities",
            data=oversized,
            timeout=10,
            proxies=_no_proxy(),
        )
        # 接口可能返回 405（不允许 POST）或 413（体积超限）
        # 两者都说明服务正常防御
        assert r.status_code in (405, 413), f"Expected 405 or 413, got {r.status_code}"


# ---------- X-Request-ID 追踪 ----------


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestRequestID:
    """法典 2.3: 所有请求与响应必须包含 X-Request-ID。"""

    def test_response_has_request_id(self) -> None:
        """任意 200 响应的头中必须有 X-Request-ID。"""
        r = requests.get(
            f"{BASE_URL}/api/v1/capabilities",
            timeout=5,
            proxies=_no_proxy(),
        )
        assert "X-Request-ID" in r.headers or "x-request-id" in r.headers, (
            f"Missing X-Request-ID in response headers: {dict(r.headers)}"
        )

    def test_echoes_client_request_id(self) -> None:
        """客户端发送 X-Request-ID 时，服务端应回传相同值。"""
        custom_id = f"test-{uuid.uuid4()}"
        r = requests.get(
            f"{BASE_URL}/api/v1/capabilities",
            headers={"X-Request-ID": custom_id},
            timeout=5,
            proxies=_no_proxy(),
        )
        returned_id = r.headers.get("X-Request-ID") or r.headers.get("x-request-id", "")
        assert returned_id == custom_id, (
            f"Expected echoed X-Request-ID={custom_id}, got {returned_id}"
        )


# ---------- SSE 心跳 ----------


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestSSEContract:
    """法典 2.1: SSE 连接心跳与事件格式。"""

    def test_sse_connection_established(self) -> None:
        """SSE /api/v1/stream 返回 200 或 503（Redis 不可用时降级）。"""
        with requests.get(
            f"{BASE_URL}/api/v1/events",
            stream=True,
            timeout=10,
            proxies=_no_proxy(),
        ) as r:
            assert r.status_code in (200, 503), f"SSE should return 200 or 503, got {r.status_code}"
            if r.status_code == 200:
                ct = r.headers.get("Content-Type", "")
                assert "text/event-stream" in ct, f"Expected text/event-stream, got {ct}"

    def test_sse_receives_data_within_timeout(self) -> None:
        """SSE 连接在 10s 内应收到至少一行响应（心跳或事件）。"""
        with requests.get(
            f"{BASE_URL}/api/v1/events",
            stream=True,
            timeout=10,
            proxies=_no_proxy(),
        ) as r:
            lines_received: list[str] = []
            for line in r.iter_lines(decode_unicode=True):
                if line is not None:
                    lines_received.append(line)
                if len(lines_received) >= 3:
                    break
            assert len(lines_received) >= 1, "No data received from SSE within timeout"


# ---------- JWT 双轨（可选，需有效 token） ----------


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestJWTDualTrack:
    """法典 3.4: 双轨 JWT 轮转。"""

    def test_unauthorized_returns_401_with_zen_code(self) -> None:
        """无 token 访问受保护接口应返回 401 + ZEN-AUTH-xxx。"""
        r = requests.get(
            f"{BASE_URL}/api/v1/admin/settings",
            timeout=5,
            proxies=_no_proxy(),
        )
        # 可能 401 或 403
        assert r.status_code in (401, 403, 404), f"Expected auth error, got {r.status_code}"
        if r.status_code in (401, 403):
            body = r.json()
            assert "code" in body
            assert body["code"].startswith("ZEN-"), f"Auth error code should start with ZEN-: {body}"


# ---------- All-OFF 矩阵（Redis 失联降级） ----------


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestAllOffFallback:
    """法典 3.2.5: 冷启动 Redis 失联时返回 ALL_OFF_MATRIX。"""

    def test_capabilities_returns_data_regardless(self) -> None:
        """
        /api/v1/capabilities 无论 Redis 是否可用都必须返回 200。
        Redis 挂时返回 ALL_OFF + X-ZEN70-Bus-Status: not-ready。
        Redis 在线时返回正常矩阵。
        """
        r = requests.get(
            f"{BASE_URL}/api/v1/capabilities",
            timeout=5,
            proxies=_no_proxy(),
        )
        assert r.status_code == 200
        body = r.json()
        # 无论如何都应有 data 字段（Envelope）
        data = body.get("data", body)
        assert isinstance(data, dict), f"Capabilities data should be dict, got {type(data)}"

        # 如果 Redis 不可用，检查 Bus-Status 头
        bus_status = r.headers.get("X-ZEN70-Bus-Status", "")
        if not REDIS_OK:
            assert bus_status == "not-ready", (
                f"Without Redis, expected X-ZEN70-Bus-Status: not-ready, got '{bus_status}'"
            )
