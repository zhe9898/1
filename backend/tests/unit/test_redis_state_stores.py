from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.platform.redis.client import Capability, NodeInfo
from backend.tests.unit.redis_test_utils import make_client, make_connected_client


class TestCapabilityStore:
    @pytest.mark.asyncio
    async def test_defaults_when_not_connected(self) -> None:
        client = make_client()
        assert await client.capabilities.get_all() == {}
        assert await client.capabilities.set("svc", {"endpoint": "http://x", "status": "online"}) is False
        assert await client.capabilities.delete("svc") is False

    @pytest.mark.asyncio
    async def test_set_get_delete(self) -> None:
        client = make_connected_client()
        capability: Capability = {
            "endpoint": "http://test:8000",
            "models": ["model1"],
            "status": "online",
            "reason": None,
        }
        client._redis.hset = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await client.capabilities.set("svc", capability) is True

        client._redis.hgetall = AsyncMock(return_value={"svc": json.dumps(capability)})  # type: ignore[method-assign, union-attr]
        assert (await client.capabilities.get_all())["svc"]["status"] == "online"

        client._redis.hdel = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        assert await client.capabilities.delete("svc") is True


class TestSwitchAndNodeStores:
    @pytest.mark.asyncio
    async def test_switch_store(self) -> None:
        client = make_connected_client()
        client._redis.hset = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]

        with patch("backend.platform.redis.switch_store.AsyncEventPublisher") as publisher_cls:
            publisher = publisher_cls.return_value
            publisher.publish_signal = AsyncMock(return_value=1)
            publisher.publish_control = AsyncMock(return_value=True)
            publisher.close = AsyncMock(return_value=None)
            assert await client.switches.set("media", "ON", reason="test", updated_by="unit") is True

        publisher.publish_signal.assert_awaited_once()
        publisher.publish_control.assert_awaited_once()
        publisher.close.assert_awaited_once()

        client._redis.hgetall = AsyncMock(  # type: ignore[method-assign, union-attr]
            return_value={
                "state": "ON",
                "reason": "test",
                "updated_at": "1711111111.0",
                "updated_by": "unit",
            }
        )
        state = await client.switches.get("media")
        assert state is not None
        assert state["state"] == "ON"

    @pytest.mark.asyncio
    async def test_hardware_store_uses_control_event_bus_publish_path(self) -> None:
        client = make_connected_client()
        client._redis.hset = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]

        with patch("backend.platform.redis.hardware_store.AsyncEventPublisher") as publisher_cls:
            publisher = publisher_cls.return_value
            publisher.publish_control = AsyncMock(return_value=True)
            publisher.close = AsyncMock(return_value=None)
            assert await client.hardware.set("/mnt/media", "online", reason="mounted", uuid_val="disk-1") is True

        publisher.publish_control.assert_awaited_once()
        publisher.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_node_store_register_and_get(self) -> None:
        client = make_connected_client()
        info: NodeInfo = {
            "node_id": "test-node",
            "hostname": "host1",
            "role": "worker",
            "capabilities": ["cpu", "gpu"],
            "resources": {"cpu_cores": 8, "ram_mb": 16384},
            "endpoint": "http://host1:8000",
            "load": {"cpu_percent": 10.0, "gpu_percent": 0.0},
        }
        client._redis.hset = AsyncMock(return_value=1)  # type: ignore[method-assign, union-attr]
        client._redis.expire = AsyncMock(return_value=True)  # type: ignore[method-assign, union-attr]
        assert await client.nodes.register("test-node", info) is True

        client._redis.hgetall = AsyncMock(  # type: ignore[method-assign, union-attr]
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
        node = await client.nodes.get("test-node")
        assert node is not None
        assert node.get("hostname") == "host1"

    @pytest.mark.asyncio
    async def test_node_store_get_all_empty(self) -> None:
        client = make_connected_client()

        async def _scan_iter(*args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            if False:
                yield ""

        client._redis.scan_iter = _scan_iter  # type: ignore[method-assign, union-attr]
        assert await client.nodes.get_all() == {}


class TestAuthChallenges:
    @pytest.mark.asyncio
    async def test_store_and_consume(self) -> None:
        client = make_connected_client()
        client._redis.setex = AsyncMock(return_value=True)  # type: ignore[method-assign, union-attr]
        assert await client.auth_challenges.store("challenge", "value", ttl_seconds=300) is True

        mock_pipe = AsyncMock()
        mock_pipe.get = MagicMock()
        mock_pipe.delete = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=["value", 1])
        client._redis.pipeline = MagicMock(return_value=mock_pipe)  # type: ignore[method-assign, union-attr]
        assert await client.auth_challenges.consume("challenge") == "value"
