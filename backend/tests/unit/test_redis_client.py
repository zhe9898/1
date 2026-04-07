"""
单元测试：Redis 状态机客户端。

验证能力矩阵、软开关、锁、节点注册的 CRUD 与发布事件；
未连接或异常时返回安全默认值。

全 Mock 测试，不依赖任何外部 Redis 实例（法典 5.1：单元测试禁止外部依赖）。
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.redis_client import (
    Capability,
    NodeInfo,
    RedisClient,
)

# ==================== 辅助工厂 ====================


def _make_client(*, host: str = "localhost") -> RedisClient:
    """构造一个 RedisClient 实例（不调用 connect）。"""
    with patch.dict(os.environ, {"REDIS_HOST": host}):
        return RedisClient()


def _make_connected_client() -> RedisClient:
    """构造一个已注入 Mock _redis 的 RedisClient（模拟已连接状态）。"""
    c = _make_client()
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    c._redis = mock_redis
    return c


# ==================== 未连接时的降级行为 ====================


class TestNotConnectedDefaults:
    """未调用 connect 时，所有操作应返回安全默认值，不抛异常。"""

    @pytest.mark.asyncio
    async def test_read_ops_return_empty(self) -> None:
        """读操作：返回空字典、None 或 False。"""
        c = _make_client()
        assert c._redis is None

        assert await c.get_capabilities() == {}
        assert await c.get_node("any") is None
        assert await c.get_all_nodes() == {}
        assert await c.get_switch("any") is None
        assert await c.get_hardware("/mnt/x") is None
        assert await c.ping() is False

    @pytest.mark.asyncio
    async def test_write_ops_return_false(self) -> None:
        """写操作：返回 False。"""
        c = _make_client()
        assert await c.set_capability("x", {"endpoint": "http://x", "status": "online"}) is False
        assert await c.set_switch("x", "ON") is False
        assert await c.acquire_lock("x") is False  # type: ignore[unreachable]
        assert await c.is_locked("x") is False
        assert await c.release_lock("x") is False
        assert await c.publish("ch", "msg") == 0

    @pytest.mark.asyncio
    async def test_proxy_ops_return_safe_values(self) -> None:
        """底层代理命令（get/set/delete/incr/expire）：安全降级。"""
        c = _make_client()
        assert await c.get("key") is None
        assert await c.set("key", "val") is None
        assert await c.setex("key", 60, "val") is None
        assert await c.delete("key") == 0
        assert await c.incr("key") == 0
        assert await c.expire("key", 60) is False
        assert c.pubsub() is None


# ==================== 能力矩阵 ====================


class TestCapabilitiesCrud:
    """能力矩阵：设置、读取、删除（Mock Redis）。"""

    @pytest.mark.asyncio
    async def test_set_and_get_capability(self) -> None:
        """set_capability → get_capabilities 能正确存取。"""
        c = _make_connected_client()
        cap: Capability = {
            "endpoint": "http://test:8000",
            "models": ["model1"],
            "status": "online",
            "reason": None,
        }

        # Mock hset 返回成功
        c._redis.hset = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await c.set_capability("test_svc", cap) is True
        c._redis.hset.assert_awaited_once()  # type: ignore[union-attr]

        # Mock hgetall 返回存储的数据
        c._redis.hgetall = AsyncMock(return_value={"test_svc": json.dumps(cap)})  # type: ignore[method-assign, union-attr]
        caps = await c.get_capabilities()
        assert "test_svc" in caps
        assert caps["test_svc"]["endpoint"] == "http://test:8000"
        assert caps["test_svc"]["status"] == "online"

    @pytest.mark.asyncio
    async def test_delete_capability(self) -> None:
        """delete_capability 应调用 hdel。"""
        c = _make_connected_client()
        c._redis.hdel = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await c.delete_capability("test_svc") is True
        c._redis.hdel.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_get_capabilities_empty(self) -> None:
        """Redis 返回空 hash 时，应返回空字典。"""
        c = _make_connected_client()
        c._redis.hgetall = AsyncMock(return_value={})  # type: ignore[method-assign, union-attr]
        caps = await c.get_capabilities()
        assert caps == {}

    @pytest.mark.asyncio
    async def test_get_capabilities_invalid_json(self) -> None:
        """某个 capability 的 value 非法 JSON 时，应跳过该条目。"""
        c = _make_connected_client()
        c._redis.hgetall = AsyncMock(  # type: ignore[method-assign, union-attr]
            return_value={
                "good": json.dumps({"endpoint": "http://x", "status": "online"}),
                "bad": "not-json{{{",
            }
        )
        caps = await c.get_capabilities()
        assert "good" in caps
        assert "bad" not in caps


# ==================== 软开关 ====================


class TestSwitchSetGet:
    """软开关：设置后能正确读取。"""

    @pytest.mark.asyncio
    async def test_set_and_get_switch(self) -> None:
        """set_switch → get_switch 能正确存取。"""
        c = _make_connected_client()

        # Mock pipeline for set_switch
        mock_pipe = AsyncMock()
        mock_pipe.hset = MagicMock()
        mock_pipe.publish = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[True, 1])
        c._redis.pipeline = MagicMock(return_value=mock_pipe)  # type: ignore[method-assign, union-attr]

        assert await c.set_switch("test_switch", "ON", reason="test", updated_by="unit") is True

        # Mock hgetall for get_switch
        c._redis.hgetall = AsyncMock(  # type: ignore[unreachable]
            return_value={
                "state": "ON",
                "reason": "test",
                "updated_at": "1711111111.0",
                "updated_by": "unit",
            }
        )
        sw = await c.get_switch("test_switch")
        assert sw is not None
        assert sw["state"] == "ON"
        assert sw["reason"] == "test"
        assert sw["updated_by"] == "unit"
        assert sw["updated_at"] > 0

    @pytest.mark.asyncio
    async def test_get_switch_not_found(self) -> None:
        """不存在的开关返回 None。"""
        c = _make_connected_client()
        c._redis.hgetall = AsyncMock(return_value={})  # type: ignore[method-assign, union-attr]
        sw = await c.get_switch("nonexistent")
        assert sw is None


# ==================== 锁 ====================


class TestLockAcquireRelease:
    """分布式锁：获取、检查、释放。"""

    @pytest.mark.asyncio
    async def test_lock_lifecycle(self) -> None:
        """锁的完整生命周期：acquire → is_locked → acquire(重复) → release → is_locked。"""
        c = _make_connected_client()

        # 首次获取成功
        c._redis.set = AsyncMock(return_value=True)  # type: ignore[method-assign, union-attr]
        assert await c.acquire_lock("test_lock", ttl=10) is True

        # 检查锁存在
        c._redis.exists = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await c.is_locked("test_lock") is True

        # 重复获取失败（NX 返回 None）
        c._redis.set = AsyncMock(return_value=None)  # type: ignore[method-assign, union-attr]
        assert await c.acquire_lock("test_lock", ttl=10) is False

        # 释放锁
        c._redis.delete = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await c.release_lock("test_lock") is True

        # 锁已不存在
        c._redis.exists = AsyncMock(return_value=0)  # type: ignore[method-assign, union-attr]
        assert await c.is_locked("test_lock") is False


# ==================== Redis 故障时的降级 ====================


class TestRedisErrorDegradation:
    """Redis 操作抛异常时，返回安全默认值，不抛出异常。"""

    @pytest.mark.asyncio
    async def test_get_capabilities_error_returns_empty(self) -> None:
        """hgetall 抛异常 → 重试 1 次仍失败 → 返回空字典。"""
        c = _make_connected_client()
        c._redis.hgetall = AsyncMock(side_effect=ConnectionError("mock"))  # type: ignore[method-assign, union-attr]
        caps = await c.get_capabilities()
        assert caps == {}

    @pytest.mark.asyncio
    async def test_set_capability_error_returns_false(self) -> None:
        """hset 抛异常 → 返回 False。"""
        c = _make_connected_client()
        c._redis.hset = AsyncMock(side_effect=ConnectionError("mock"))  # type: ignore[method-assign, union-attr]
        ok = await c.set_capability("x", {"endpoint": "http://x", "status": "online"})
        assert ok is False

    @pytest.mark.asyncio
    async def test_get_switch_error_returns_none(self) -> None:
        """get_switch 底层异常 → 重试 + 返回 None。"""
        c = _make_connected_client()
        c._redis.hgetall = AsyncMock(side_effect=OSError("mock"))  # type: ignore[method-assign, union-attr]
        sw = await c.get_switch("any_switch")
        assert sw is None

    @pytest.mark.asyncio
    async def test_acquire_lock_error_returns_false(self) -> None:
        """acquire_lock 底层异常 → 返回 False。"""
        c = _make_connected_client()
        c._redis.set = AsyncMock(side_effect=RuntimeError("mock"))  # type: ignore[method-assign, union-attr]
        assert await c.acquire_lock("test_lock") is False

    @pytest.mark.asyncio
    async def test_publish_error_returns_zero(self) -> None:
        """publish 底层异常 → 返回 0。"""
        c = _make_connected_client()
        c._redis.publish = AsyncMock(side_effect=ConnectionError("mock"))  # type: ignore[method-assign, union-attr]
        assert await c.publish("ch", "msg") == 0


# ==================== 节点注册与获取 ====================


class TestNodeRegistration:
    """节点注册、获取、心跳。"""

    @pytest.mark.asyncio
    async def test_register_and_get_node(self) -> None:
        """register_node → get_node 能正确存取。"""
        c = _make_connected_client()

        info: NodeInfo = {
            "node_id": "test-node",
            "hostname": "host1",
            "role": "worker",
            "capabilities": ["cpu", "gpu"],
            "resources": {"cpu_cores": 8, "ram_mb": 16384},
            "endpoint": "http://host1:8000",
            "load": {"cpu_percent": 10.0, "gpu_percent": 0.0},
        }

        # Mock pipeline for register_node
        c._redis.hset = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        c._redis.expire = AsyncMock(return_value=True)  # type: ignore[method-assign, union-attr]
        assert await c.register_node("test-node", info) is True

        # Mock get_node 返回
        c._redis.hgetall = AsyncMock(  # type: ignore[method-assign, union-attr]
            return_value={
                "node_id": "test-node",
                "hostname": "host1",
                "role": "worker",
                "capabilities": json.dumps(["cpu", "gpu"]),
                "resources": json.dumps({"cpu_cores": 8, "ram_mb": 16384}),
                "endpoint": "http://host1:8000",
                "last_seen": "1711111111.0",
                "load": json.dumps({"cpu_percent": 10.0, "gpu_percent": 0.0}),
            }
        )
        node = await c.get_node("test-node")
        assert node is not None
        assert node.get("hostname") == "host1"
        assert node.get("role") == "worker"
        assert node.get("capabilities") == ["cpu", "gpu"]
        assert node.get("resources", {}).get("cpu_cores") == 8

    @pytest.mark.asyncio
    async def test_get_node_not_found(self) -> None:
        """不存在的节点返回 None。"""
        c = _make_connected_client()
        c._redis.hgetall = AsyncMock(return_value={})  # type: ignore[method-assign, union-attr]
        node = await c.get_node("nonexistent")
        assert node is None

    @pytest.mark.asyncio
    async def test_get_all_nodes_empty(self) -> None:
        """无节点时返回空字典。"""
        c = _make_connected_client()
        async def _scan_iter(*args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            if False:
                yield ""

        c._redis.scan_iter = _scan_iter  # type: ignore[method-assign, union-attr]
        nodes = await c.get_all_nodes()
        assert nodes == {}


# ==================== 连接与关闭 ====================


class TestConnectClose:
    """connect/close 生命周期。"""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        """connect() 成功时 _redis 不为 None。"""
        c = _make_client()
        mock_redis_instance = AsyncMock()
        mock_redis_instance.ping = AsyncMock(return_value=True)

        with patch("backend.core.redis_client.redis") as mock_redis_module:
            mock_redis_module.Redis = MagicMock(return_value=mock_redis_instance)
            await c.connect()
            _, kwargs = mock_redis_module.Redis.call_args
            assert kwargs["max_connections"] == c.max_connections

        assert c._redis is not None

    @pytest.mark.asyncio
    async def test_connect_failure_sets_none(self) -> None:
        """connect() ping 失败时 _redis 设为 None 并抛异常。"""
        c = _make_client()
        mock_redis_instance = AsyncMock()
        mock_redis_instance.ping = AsyncMock(side_effect=OSError("refused"))

        with patch("backend.core.redis_client.redis") as mock_redis_module:
            mock_redis_module.Redis = MagicMock(return_value=mock_redis_instance)
            with pytest.raises(OSError, match="refused"):
                await c.connect()

        assert c._redis is None

    @pytest.mark.asyncio
    async def test_close_clears_redis(self) -> None:
        """close() 后 _redis 应为 None。"""
        c = _make_connected_client()
        c._redis.aclose = AsyncMock()  # type: ignore[union-attr]
        await c.close()
        assert c._redis is None

    @pytest.mark.asyncio
    async def test_double_connect_noop(self) -> None:
        """已连接时再次 connect() 不重复创建。"""
        c = _make_connected_client()
        original = c._redis
        await c.connect()
        assert c._redis is original  # 未被替换


# ==================== 认证挑战 ====================


class TestAuthChallenge:
    """WebAuthn 认证挑战存储。"""

    @pytest.mark.asyncio
    async def test_set_and_get_challenge(self) -> None:
        """set_auth_challenge → get_auth_challenge 能正确存取并一次性删除。"""
        c = _make_connected_client()

        c._redis.setex = AsyncMock(return_value=True)  # type: ignore[method-assign, union-attr]
        assert await c.set_auth_challenge("chall_b64", "val", ttl=300) is True

        c._redis.get = AsyncMock(return_value="val")  # type: ignore[method-assign, union-attr]
        c._redis.delete = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        result = await c.get_auth_challenge("chall_b64")
        assert result == "val"
        c._redis.delete.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_get_challenge_not_found(self) -> None:
        """不存在的挑战返回 None，不调用 delete。"""
        c = _make_connected_client()
        c._redis.get = AsyncMock(return_value=None)  # type: ignore[method-assign, union-attr]
        c._redis.delete = AsyncMock()  # type: ignore[method-assign, union-attr]
        result = await c.get_auth_challenge("nonexistent")
        assert result is None
        c._redis.delete.assert_not_awaited()  # type: ignore[union-attr]


# ==================== incr_with_expire ====================


class TestIncrWithExpire:
    """INCR + 条件 EXPIRE。"""

    @pytest.mark.asyncio
    async def test_first_incr_sets_expire(self) -> None:
        """首次 INCR（返回 1）应设置过期时间。"""
        c = _make_connected_client()
        c._redis.incr = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        c._redis.expire = AsyncMock(return_value=True)  # type: ignore[method-assign, union-attr]
        result = await c.incr_with_expire("key", 60)
        assert result == 1
        c._redis.expire.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_subsequent_incr_no_expire(self) -> None:
        """后续 INCR（返回 >1）不应再设置过期。"""
        c = _make_connected_client()
        c._redis.incr = AsyncMock(return_value=5)  # type: ignore[method-assign, union-attr]
        c._redis.expire = AsyncMock()  # type: ignore[method-assign, union-attr]
        result = await c.incr_with_expire("key", 60)
        assert result == 5
        c._redis.expire.assert_not_awaited()  # type: ignore[union-attr]
