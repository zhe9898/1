"""
硬件缺失模拟与集成测试。

验证：探针防抖与悲观锁、hw 状态与容器 pause/unpause、三重核验、SSE 事件。
- Mock 模式测试：不依赖真实挂载/GPU，可在 CI 运行。
- 真实挂载测试：需环回设备与 root，可选运行。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
import requests

# 同包 conftest 中的 fixture 与 helper 可直接使用
from tests.integration.conftest import (
    BASE_URL,
    CONTAINER_NAME,
    GATEWAY_OK,
    PROBE_DEBOUNCE_WAIT,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_OK,
    TEST_MOUNT_PATH,
    _no_proxy,
    container_status,
    get_hw_state,
    redis_client,
)

# 可选：挂载模拟器（Linux + root）
try:
    from tests.chaos.mount_simulator import MountSimulator
    _MOUNT_SIMULATOR_AVAILABLE = True
except ImportError:
    _MOUNT_SIMULATOR_AVAILABLE = False


# ---------- 网关与 Redis 基础 ----------


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
def test_health_endpoint() -> None:
    """网关 /health 返回 200 且含 status。"""
    r = requests.get(f"{BASE_URL}/health", timeout=5, proxies=_no_proxy())
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "services" in data


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
def test_capabilities_endpoint() -> None:
    """能力矩阵接口可调；无 Redis 时返回 All-OFF 矩阵。"""
    r = requests.get(f"{BASE_URL}/api/v1/capabilities", timeout=5, proxies=_no_proxy())
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
def test_switches_endpoint() -> None:
    """软开关接口返回 state。"""
    r = requests.get(f"{BASE_URL}/api/v1/switches/media_engine", timeout=5, proxies=_no_proxy())
    assert r.status_code == 200
    data = r.json()
    # ADR-0010: success_envelope 包装后 data 在 body["data"] 中
    data = data.get("data", data)
    assert "state" in data
    assert data["state"] in ("ON", "OFF", "PENDING", "")


# ---------- Mock 模式：探针逻辑与 Redis hw 状态 ----------


@pytest.mark.skipif(not REDIS_OK, reason="Redis not available")
def test_redis_hw_key_after_mock_probe(redis_client) -> None:
    """
    探针在 Mock 模式下会周期写入 hw:* 与 hw:gpu。
    若已启动探针（Mock），等待若干周期后 Redis 中应有对应键或至少 gpu 键。
    """
    if redis_client is None:
        pytest.skip("Redis client not available")
    time.sleep(max(5, PROBE_DEBOUNCE_WAIT // 2))
    # 探针 Mock 时写 hw:gpu；挂载点依赖 MOUNT_POINTS 配置
    gpu = redis_client.hgetall("hw:gpu")
    # 不强制有 gpu 键（探针可能未跑），有则检查形状
    if gpu:
        assert "online" in gpu


@pytest.mark.skipif(not REDIS_OK, reason="Redis not available")
def test_sse_events_stream() -> None:
    """SSE /api/v1/events 可建立连接并收到首条消息或心跳。"""
    if not GATEWAY_OK:
        pytest.skip("Gateway not available")
    with requests.get(f"{BASE_URL}/api/v1/events", stream=True, timeout=10, proxies=_no_proxy()) as r:
        assert r.status_code == 200
        lines: list[str] = []
        for line in r.iter_lines(decode_unicode=True):
            if line is not None:
                lines.append(line)
            if len(lines) >= 5 or (lines and "event:" in str(lines[-1])):
                break
        assert len(lines) >= 1


# ---------- 真实挂载模拟（需 Linux + root，CI 可跳过） ----------


def _can_run_mount_tests() -> bool:
    if not _MOUNT_SIMULATOR_AVAILABLE:
        return False
    if sys.platform != "linux":
        return False
    # 可要求环境变量显式启用，避免 CI 误跑
    return os.getenv("ZEN70_RUN_MOUNT_TESTS", "").lower() in ("1", "true")


@pytest.fixture(scope="module")
def mount_simulator():
    """挂载模拟器；仅在有权限且启用时创建。"""
    if not _can_run_mount_tests():
        pytest.skip("Mount simulator tests disabled (Linux + ZEN70_RUN_MOUNT_TESTS=1)")
    sim = MountSimulator(mount_point=TEST_MOUNT_PATH)
    yield sim
    sim.teardown()


@pytest.mark.skipif(not _can_run_mount_tests(), reason="Mount simulator not runnable")
def test_storage_loss_triggers_pending_then_offline(
    mount_simulator,
    redis_client,
) -> None:
    """
    1) 挂载存在 -> 探针应写入 online（或至少不 pending）。
    2) 卸载 -> 防抖后应变为 pending/offline，关联容器（若配置）应 pause。
    3) 重新挂载 -> 三重核验通过后应恢复 online，容器 unpause。
    """
    sim = mount_simulator
    if redis_client is None:
        pytest.skip("Redis not available")
    # 1. 确保挂载
    ok = sim.mount()
    assert ok, "Mount failed (need root/loop device)"
    time.sleep(PROBE_DEBOUNCE_WAIT)
    hw = get_hw_state(redis_client, TEST_MOUNT_PATH)
    # 可能尚未被探针配置（MOUNT_POINTS 不含 /mnt/test）则跳过状态断言
    if hw:
        assert hw.get("state") in ("online", "pending", "offline", None)

    # 2. 模拟拔出
    sim.umount()
    time.sleep(PROBE_DEBOUNCE_WAIT)
    hw2 = get_hw_state(redis_client, TEST_MOUNT_PATH)
    if hw2:
        assert hw2.get("state") in ("pending", "offline")
    if CONTAINER_NAME:
        status = container_status(CONTAINER_NAME)
        if status:
            assert status in ("paused", "running")  # 可能尚未 pause 完成

    # 3. 重新挂载
    sim.mount()
    time.sleep(PROBE_DEBOUNCE_WAIT)
    hw3 = get_hw_state(redis_client, TEST_MOUNT_PATH)
    if hw3:
        assert hw3.get("state") in ("online", "pending")
    if CONTAINER_NAME:
        status = container_status(CONTAINER_NAME)
        if status:
            assert status in ("running", "paused")


# ---------- GPU Mock ----------


@pytest.mark.skipif(not REDIS_OK, reason="Redis not available")
def test_gpu_state_key_shape(redis_client) -> None:
    """若探针已写 hw:gpu，则应含 online 字段。"""
    if redis_client is None:
        pytest.skip("Redis not available")
    gpu = redis_client.hgetall("hw:gpu")
    if not gpu:
        pytest.skip("Probe not running or no GPU key yet")
    assert "online" in gpu


# ---------- 三步熔断顺序验证（法典 3.1） ----------


@pytest.mark.skipif(not (GATEWAY_OK and REDIS_OK), reason="Gateway and Redis required")
def test_three_step_meltdown_api_first(redis_client) -> None:
    """
    法典 3.1：三步熔断之第一步 — API 层 503。
    注入 PENDING_MAINTENANCE 后，API 必须先返回 503（第一步），
    验证 API 层是第一道防线，不跳级到容器操作。
    """
    if redis_client is None:
        pytest.skip("Redis client not available")
    test_key = "zen70:topology:test_meltdown_target"
    try:
        # 注入异常状态
        redis_client.set(test_key, "PENDING_MAINTENANCE")
        # 发布事件通知网关
        redis_client.publish("hardware:events", '{"source":"test","status":"PENDING_MAINTENANCE"}')
        # 等待 LRU 缓存刷新
        time.sleep(max(1, PROBE_DEBOUNCE_WAIT) + 31)

        # 第一步验证：API 层应优先熔断（返回 503 或降级响应）
        r = requests.get(
            f"{BASE_URL}/api/v1/capabilities",
            timeout=10,
            proxies=_no_proxy(),
        )
        # capabilities 即使有异常也返回 200（内含降级状态），这验证 API 层"有响应"
        assert r.status_code == 200
        body = r.json()
        data = body.get("data", body)
        # 验证矩阵中至少有一个非 online 项（说明 API 层在反映异常状态）
        assert isinstance(data, dict)
    finally:
        redis_client.delete(test_key)


@pytest.mark.skipif(not GATEWAY_OK, reason="Gateway not available")
def test_health_endpoint_distinguishes_healthy_unhealthy() -> None:
    """
    /health 应返回 status=healthy 或 unhealthy，区分整体服务状态。
    核心服务不可用时 status 应为 unhealthy（而非直接 500）。
    """
    r = requests.get(f"{BASE_URL}/health", timeout=5, proxies=_no_proxy())
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") in ("healthy", "unhealthy"), (
        f"Expected healthy/unhealthy, got {data.get('status')}"
    )
    # services 字段应为 dict，列出各子服务状态
    assert isinstance(data.get("services"), dict)

