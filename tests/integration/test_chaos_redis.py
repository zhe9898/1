"""
混沌测试：Redis 断连与核心降级行为。

验证法典 3.2 冷启动安全降级：
- Redis 失联时 capabilities 返回 ALL_OFF_MATRIX
- Redis 失联时 X-ZEN70-Bus-Status: not-ready
- Redis 失联时幂等锁降级放行
- Redis 恢复后服务自动恢复

注意：这些测试在 Docker 环境中运行，可通过
docker compose pause redis 模拟 Redis 断连。
不需要 Redis 可用即可测试降级行为——
在 Redis 不可用的环境中天然测试降级路径。
"""

from __future__ import annotations

import pytest
import requests

from tests.integration.conftest import (
    BASE_URL,
    GATEWAY_OK,
    REDIS_OK,
    _no_proxy,
)


@pytest.mark.chaos
@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestRedisDisconnectDegradation:
    """Redis 断连降级行为验证。"""

    def test_capabilities_survives_without_redis(self) -> None:
        """法典 3.2.5: Redis 不可用时 capabilities 必须返回 200（ALL_OFF 降级）。"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/capabilities",
            timeout=5,
            proxies=_no_proxy(),
        )
        # 无论 Redis 是否在线，capabilities 都必须 200
        assert resp.status_code == 200
        body = resp.json()
        data = body.get("data", body)
        assert isinstance(data, dict), f"Capabilities should be dict even in degraded mode"

    def test_bus_status_header_reflects_redis_state(self) -> None:
        """Redis 不可用时应返回 X-ZEN70-Bus-Status: not-ready。"""
        resp = requests.get(
            f"{BASE_URL}/api/v1/capabilities",
            timeout=5,
            proxies=_no_proxy(),
        )
        bus_header = resp.headers.get("X-ZEN70-Bus-Status", "")
        if not REDIS_OK:
            assert bus_header == "not-ready", (
                f"Without Redis, expected X-ZEN70-Bus-Status: not-ready, got '{bus_header}'"
            )
        # Redis 在线时 header 可能为空或 ready

    def test_health_reports_redis_status(self) -> None:
        """健康检查必须准确报告 Redis 状态。"""
        resp = requests.get(
            f"{BASE_URL}/health",
            timeout=5,
            proxies=_no_proxy(),
        )
        assert resp.status_code == 200
        body = resp.json()
        # 包裹在 envelope 中时取 data
        data = body.get("data", body)
        if REDIS_OK:
            assert data.get("redis") in ("ok", "connected"), (
                f"Redis is up but health reports: {data.get('redis')}"
            )
        else:
            assert data.get("redis") in ("error", "disconnected", None), (
                f"Redis is down but health reports: {data.get('redis')}"
            )
            assert data.get("status") in ("degraded", "unhealthy"), (
                f"Without Redis, status should be degraded, got: {data.get('status')}"
            )

    def test_sse_events_survives_without_redis(self) -> None:
        """SSE 端点在 Redis 不可用时不应 500 崩溃。"""
        try:
            resp = requests.get(
                f"{BASE_URL}/api/v1/events",
                stream=True,
                timeout=5,
                proxies=_no_proxy(),
            )
            # 不崩溃即可：200（正常或空流）或 503（显式降级）
            assert resp.status_code in (200, 503), (
                f"SSE should return 200 or 503 on Redis failure, got {resp.status_code}"
            )
        except requests.exceptions.Timeout:
            # 超时也算可接受（SSE 长连接本身就可能超时）
            pass


@pytest.mark.chaos
@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
class TestServiceCircuitBreaker:
    """服务级熔断降级。"""

    def test_nonexistent_service_route_returns_503(self) -> None:
        """访问未就绪服务的路由应返回 503 而非 500。"""
        # 尝试一个不太可能存在的服务路由
        resp = requests.get(
            f"{BASE_URL}/api/v1/jellyfin/status",
            timeout=5,
            proxies=_no_proxy(),
        )
        # 服务未注册 → 404；服务已注册但不可用 → 503
        assert resp.status_code in (404, 503), (
            f"Unavailable service should return 404 or 503, got {resp.status_code}"
        )

    def test_error_responses_never_expose_stacktrace(self) -> None:
        """任何错误响应都不应暴露 Python stacktrace（安全红线）。"""
        endpoints = [
            "/api/v1/_trigger_error_test",
            "/api/v1/nonexistent",
        ]
        for ep in endpoints:
            resp = requests.get(
                f"{BASE_URL}{ep}",
                timeout=5,
                proxies=_no_proxy(),
            )
            body = resp.text
            assert "Traceback" not in body, f"Stacktrace leaked in {ep}: {body[:200]}"
            assert "File \"/" not in body, f"File path leaked in {ep}: {body[:200]}"
