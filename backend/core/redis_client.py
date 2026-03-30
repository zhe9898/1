"""
ZEN70 Redis 状态机访问层。

提供能力矩阵、节点状态、软开关、硬件状态、锁及发布/订阅的统一接口；
键名与频道规范见模块常量。供网关、探针、调度器使用。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypedDict, TypeVar, cast

try:
    import redis.asyncio as redis
    from redis.asyncio import Redis
except ImportError:
    redis = None  # type: ignore
    Redis = None  # type: ignore

# -------------------- 键与频道常量 --------------------
KEY_CAPABILITIES = "capabilities"
KEY_NODE_PREFIX = "cluster:nodes:"
KEY_SWITCH_PREFIX = "switch:"
# Phase 1 SSOT: 从统一常量注册表导入，严禁本地硬编码
from backend.core.constants import (  # noqa: E402
    CHANNEL_CONNECTOR_EVENTS,
    CHANNEL_HARDWARE_EVENTS,
    CHANNEL_JOB_EVENTS,
    CHANNEL_NODE_EVENTS,
    CHANNEL_SWITCH_EVENTS,
    KEY_AUTH_CHALLENGE_PREFIX,
    KEY_HW_PREFIX,
    KEY_LOCK_PREFIX,
)

# 向后兼容重导出（供外部已使用 `from redis_client import CHANNEL_...` 的模块）
__all__ = [
    "CHANNEL_HARDWARE_EVENTS",
    "CHANNEL_SWITCH_EVENTS",
    "CHANNEL_NODE_EVENTS",
    "CHANNEL_JOB_EVENTS",
    "CHANNEL_CONNECTOR_EVENTS",
    "KEY_HW_PREFIX",
    "KEY_LOCK_PREFIX",
    "KEY_AUTH_CHALLENGE_PREFIX",
    "RedisClient",
]

# -------------------- 日志（复用集中模块） --------------------
from backend.core.structured_logging import get_logger  # noqa: E402

# -------------------- 数据结构 (TypedDict) --------------------


class Capability(TypedDict, total=False):
    """单个能力描述。"""

    endpoint: str
    models: list[str] | None
    status: str  # online/offline/unknown
    reason: str | None


class NodeInfo(TypedDict, total=False):
    """节点信息。"""

    node_id: str
    hostname: str
    role: str  # master/worker
    capabilities: list[str]
    resources: dict[str, object]
    endpoint: str
    last_seen: float
    load: dict[str, float]


class SwitchState(TypedDict, total=False):
    """软开关状态。"""

    state: str  # ON/OFF/PENDING
    reason: str | None
    updated_at: float
    updated_by: str | None


class HardwareState(TypedDict, total=False):
    """硬件状态（与探针写入格式一致）。"""

    path: str
    uuid: str | None
    state: str  # online/offline/pending
    timestamp: float
    reason: str | None


# -------------------- Redis 客户端 --------------------


def _node_to_redis(info: NodeInfo) -> dict[str, str]:
    """将 NodeInfo 转为 Redis HSET 可用的 str 字典。"""
    out: dict[str, str] = {}
    for k, v in info.items():
        if v is None:
            continue
        if k in ("capabilities", "resources", "load"):
            out[k] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        elif k == "last_seen":
            out[k] = str(float(cast(Any, v)))
        else:
            out[k] = str(v)
    return out


def _redis_to_node(data: dict[str, str]) -> NodeInfo:
    """将 Redis HGETALL 结果转为 NodeInfo。"""
    out: dict[str, object] = {}
    for k, v in data.items():
        if not v:
            continue
        if k in ("capabilities", "resources", "load"):
            try:
                out[k] = json.loads(v)
            except json.JSONDecodeError:
                out[k] = v
        elif k == "last_seen":
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = 0.0
        else:
            out[k] = v
    return out  # type: ignore


class RedisClient:
    """
    Redis 状态机客户端：能力矩阵、节点、软开关、硬件状态、锁；所有操作带异常捕获与安全默认值。
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        password: str | None = None,
        db: int | None = None,
        request_id: str | None = None,
        username: str | None = None,
    ):
        h = host or os.getenv("REDIS_HOST")
        if not h:
            raise RuntimeError("REDIS_HOST env var is required")
        self.host = h
        self.port = port if port is not None else int(os.getenv("REDIS_PORT", "6379"))
        self.password = password if password is not None else os.getenv("REDIS_PASSWORD") or None
        self.username = username if username is not None else os.getenv("REDIS_USER") or None
        self.db = db if db is not None else int(os.getenv("REDIS_DB", "0"))
        self.logger = get_logger("redis_client", request_id)
        self._redis: Redis | None = None

    async def connect(self) -> None:
        """建立连接（使用 redis 内置连接池）。"""
        if redis is None:
            raise RuntimeError("redis package not installed (pip install redis)")
        if self._redis is not None:
            return
        self._redis = redis.Redis(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            db=self.db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            socket_keepalive=True,
        )
        try:
            await self._redis.ping()
            self.logger.info("Connected to Redis")
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Redis connection failed: %s", e, exc_info=True)
            self._redis = None
            raise

    async def close(self) -> None:
        """关闭连接。"""
        if self._redis:
            await self._redis.close()
            self._redis = None
            self.logger.info("Redis connection closed")

    async def ping(self) -> bool:
        """健康检查：Redis 可达返回 True。"""
        if not self._redis:
            return False
        try:
            await self._redis.ping()
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.debug("Redis ping failed: %s", e)
            return False

    @property
    def redis(self) -> Redis | None:
        """
        兼容属性：返回底层 redis.asyncio.Redis 实例。
        说明：部分旧代码使用 `redis.redis` 访问底层客户端；统一对齐到此属性。
        """
        return self._redis

    # -------------------- 底层命令代理（auth/sentinel 等模块直接调用）--------------------

    async def get(self, key: str) -> str | None:
        """代理 Redis GET。"""
        if not self._redis:
            return None
        return await self._redis.get(key)

    async def set(self, key: str, value: str | bytes | int | float, **kwargs: Any) -> object:
        """代理 Redis SET。"""
        if not self._redis:
            return None
        return await self._redis.set(key, value, **kwargs)

    async def setex(self, key: str, time_seconds: int, value: str | bytes | int | float) -> object:
        """代理 Redis SETEX（设置带 TTL 的键值）。"""
        if not self._redis:
            return None
        return await self._redis.setex(key, time_seconds, value)

    async def delete(self, *keys: str) -> int:
        """代理 Redis DELETE。"""
        if not self._redis:
            return 0
        return await self._redis.delete(*keys)

    async def incr(self, key: str) -> int:
        """代理 Redis INCR。"""
        if not self._redis:
            return 0
        return await self._redis.incr(key)

    async def expire(self, key: str, time_seconds: int) -> bool:
        """代理 Redis EXPIRE。"""
        if not self._redis:
            return False
        return await self._redis.expire(key, time_seconds)

    async def publish(self, channel: str, message: str) -> int:
        """发布消息到 Redis 频道；未连接时返回 0。"""
        if not self._redis:
            return 0
        try:
            return await self._redis.publish(channel, message)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to publish to %s: %s", channel, e, exc_info=True)
            return 0

    def pubsub(self) -> object | None:
        """返回底层 Redis 的 PubSub，用于 SSE 订阅（如 hardware:events, switch:events）。"""
        if not self._redis:
            return None
        return self._redis.pubsub()

    # -------------------- 能力矩阵 --------------------

    _T = TypeVar("_T")

    async def _retry_once(self, coro: Callable[[], Coroutine[Any, Any, _T]], fallback: _T, op_name: str = "op") -> _T:
        """关键读操作：失败时重试 1 次（间隔 0.1s），仍失败返回 fallback。"""
        try:
            return await coro()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.warning("%s failed, retrying once: %s", op_name, e)
            await asyncio.sleep(0.1)
        try:
            return await coro()
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("%s failed after retry: %s", op_name, e, exc_info=True)
            return fallback

    async def get_capabilities(self) -> dict[str, Capability]:
        """获取能力矩阵；Redis 不可用或异常时返回空字典（含 1 次重试）。"""
        if not self._redis:
            self.logger.error("Redis not connected")
            return {}

        async def _get() -> dict[str, Capability]:
            data = await self._redis.hgetall(KEY_CAPABILITIES)  # type: ignore[union-attr]
            if not data:
                return {}
            result: dict[str, Capability] = {}
            for key, value in data.items():
                try:
                    result[key] = json.loads(value)
                except json.JSONDecodeError:
                    self.logger.warning("Invalid JSON for capability %s: %s", key, value)
            return result

        return await self._retry_once(_get, {}, "get_capabilities")

    async def set_capability(self, name: str, capability: Capability) -> bool:
        """设置单个能力。"""
        if not self._redis:
            return False
        try:
            await self._redis.hset(KEY_CAPABILITIES, name, json.dumps(capability))
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to set capability %s: %s", name, e, exc_info=True)
            return False

    async def delete_capability(self, name: str) -> bool:
        """删除能力。"""
        if not self._redis:
            return False
        try:
            await self._redis.hdel(KEY_CAPABILITIES, name)
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to delete capability %s: %s", name, e, exc_info=True)
            return False

    # -------------------- 节点状态 --------------------

    async def register_node(self, node_id: str, info: NodeInfo) -> bool:
        """注册或更新节点信息；设置心跳过期时间。"""
        key = f"{KEY_NODE_PREFIX}{node_id}"
        if not self._redis:
            return False
        try:
            info_dict: dict[str, object] = dict(info)
            info_dict["last_seen"] = time.time()
            mapping = _node_to_redis(cast(NodeInfo, info_dict))
            await self._redis.hset(key, mapping=mapping)  # type: ignore[arg-type]
            await self._redis.expire(key, 60)
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to register node %s: %s", node_id, e, exc_info=True)
            return False

    async def get_node(self, node_id: str) -> NodeInfo | None:
        """获取节点信息。"""
        key = f"{KEY_NODE_PREFIX}{node_id}"
        if not self._redis:
            return None
        try:
            data = await self._redis.hgetall(key)
            if not data:
                return None
            return _redis_to_node(data)
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to get node %s: %s", node_id, e, exc_info=True)
            return None

    async def get_all_nodes(self) -> dict[str, NodeInfo]:
        """获取所有节点。"""
        if not self._redis:
            return {}
        try:
            keys = await self._redis.keys(f"{KEY_NODE_PREFIX}*")
            result: dict[str, NodeInfo] = {}
            for key in keys:
                nid = key[len(KEY_NODE_PREFIX) :] if key.startswith(KEY_NODE_PREFIX) else key.split(":")[-1]
                node = await self.get_node(nid)
                if node:
                    result[nid] = node
            return result
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to get all nodes: %s", e, exc_info=True)
            return {}

    async def heartbeat(self, node_id: str, load: dict[str, float]) -> bool:
        """节点心跳：更新 last_seen 与 load，刷新过期时间。"""
        key = f"{KEY_NODE_PREFIX}{node_id}"
        if not self._redis:
            return False
        try:
            pipe = self._redis.pipeline()
            pipe.hset(key, "last_seen", str(time.time()))
            pipe.hset(key, "load", json.dumps(load))
            pipe.expire(key, 60)
            await pipe.execute()
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to heartbeat node %s: %s", node_id, e, exc_info=True)
            return False

    # -------------------- 软开关 --------------------

    async def get_switch(self, name: str) -> SwitchState | None:
        """获取软开关状态；失败时重试 1 次。"""
        key = f"{KEY_SWITCH_PREFIX}{name}"
        if not self._redis:
            return None

        async def _get() -> SwitchState | None:
            data = await self._redis.hgetall(key)  # type: ignore[union-attr]
            if not data:
                return None
            return {
                "state": data.get("state", ""),
                "reason": data.get("reason"),
                "updated_at": float(data.get("updated_at", 0)),
                "updated_by": data.get("updated_by"),
            }

        return await self._retry_once(_get, None, f"get_switch({name})")

    async def get_all_switches(self) -> dict[str, SwitchState]:
        """获取所有软开关状态。"""
        if not self._redis:
            return {}
        try:
            keys = await self._redis.keys(f"{KEY_SWITCH_PREFIX}*")
            if not keys:
                return {}

            result: dict[str, SwitchState] = {}
            # 使用 pipeline 批量获取所有开关信息以提升性能
            pipe = self._redis.pipeline()
            for key in keys:
                pipe.hgetall(key)
            results = await pipe.execute()

            for key, data in zip(keys, results):
                if data:
                    name = key[len(KEY_SWITCH_PREFIX) :] if key.startswith(KEY_SWITCH_PREFIX) else key.split(":")[-1]
                    result[name] = {
                        "state": data.get("state", ""),
                        "reason": data.get("reason"),
                        "updated_at": float(data.get("updated_at", 0)),
                        "updated_by": data.get("updated_by"),
                    }
            return result
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to get_all_switches: %s", e, exc_info=True)
            return {}

    async def set_switch(
        self,
        name: str,
        state: str,
        reason: str = "",
        updated_by: str = "system",
    ) -> bool:
        """设置软开关并发布 switch:events。"""
        key = f"{KEY_SWITCH_PREFIX}{name}"
        if not self._redis:
            return False
        try:
            payload: dict[str, str] = {
                "state": state,
                "reason": reason,
                "updated_at": str(time.time()),
                "updated_by": updated_by,
            }
            event: dict[str, object] = {"switch": name, "name": name, **payload}
            pipe = self._redis.pipeline()
            pipe.hset(key, mapping=payload)  # type: ignore[arg-type]
            pipe.publish(CHANNEL_SWITCH_EVENTS, json.dumps(event))
            await pipe.execute()
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to set switch %s: %s", name, e, exc_info=True)
            return False

    # -------------------- 硬件状态 --------------------

    async def get_hardware(self, path: str) -> HardwareState | None:
        """获取硬件状态。"""
        key = f"{KEY_HW_PREFIX}{path}"
        if not self._redis:
            return None
        try:
            data = await self._redis.hgetall(key)
            if not data:
                return None
            hw: HardwareState = {
                "path": data.get("path", path),
                "uuid": data.get("uuid"),
                "state": data.get("state", ""),
                "timestamp": float(data.get("timestamp", 0)),
                "reason": data.get("reason"),
            }
            return hw
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to get hardware %s: %s", path, e, exc_info=True)
            return None

    async def set_hardware(
        self,
        path: str,
        state: str,
        reason: str = "",
        uuid_val: str | None = None,
    ) -> None:
        """更新硬件状态并发布 hardware:events。"""
        key = f"{KEY_HW_PREFIX}{path}"
        if not self._redis:
            return False  # type: ignore[return-value]
        try:
            ts = time.time()
            payload: dict[str, str] = {
                "path": path,
                "uuid": uuid_val or "",
                "state": state,
                "timestamp": str(ts),
                "reason": reason,
            }
            event = dict(payload, timestamp=ts)  # JSON 序列化时 timestamp 为 float
            pipe = self._redis.pipeline()
            pipe.hset(key, mapping=payload)  # type: ignore[arg-type]
            pipe.publish(CHANNEL_HARDWARE_EVENTS, json.dumps(event))
            await pipe.execute()
            return True  # type: ignore[return-value]
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to set hardware %s: %s", path, e, exc_info=True)
            return False  # type: ignore[return-value]

    # -------------------- 锁 --------------------

    async def acquire_lock(self, name: str, ttl: int = 20) -> bool:
        """获取分布式锁（非阻塞）；成功返回 True。"""
        key = f"{KEY_LOCK_PREFIX}{name}"
        if not self._redis:
            return False
        try:
            result = await self._redis.set(key, "locked", nx=True, ex=ttl)
            return result is True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to acquire lock %s: %s", name, e, exc_info=True)
            return False

    async def release_lock(self, name: str) -> bool:
        """释放锁。"""
        key = f"{KEY_LOCK_PREFIX}{name}"
        if not self._redis:
            return False
        try:
            await self._redis.delete(key)
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to release lock %s: %s", name, e, exc_info=True)
            return False

    async def is_locked(self, name: str) -> bool:
        """检查锁是否存在。"""
        key = f"{KEY_LOCK_PREFIX}{name}"
        if not self._redis:
            return False
        try:
            n = await self._redis.exists(key)
            return n > 0
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to check lock %s: %s", name, e, exc_info=True)
            return False

    # -------------------- 认证挑战（WebAuthn） --------------------

    async def set_auth_challenge(self, challenge_b64: str, value: str, ttl: int = 300) -> bool:
        """存储认证挑战，用于 register/login 完成时校验。"""
        key = f"{KEY_AUTH_CHALLENGE_PREFIX}{challenge_b64}"
        if not self._redis:
            return False
        try:
            await self._redis.setex(key, ttl, value)
            return True
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to set auth challenge: %s", e, exc_info=True)
            return False

    async def get_auth_challenge(self, challenge_b64: str) -> str | None:
        """获取并删除挑战（一次性使用）。"""
        key = f"{KEY_AUTH_CHALLENGE_PREFIX}{challenge_b64}"
        if not self._redis:
            return None
        try:
            value = await self._redis.get(key)
            if value:
                await self._redis.delete(key)
            return value
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to get auth challenge: %s", e, exc_info=True)
            return None

    async def incr_with_expire(self, key: str, window_sec: int) -> int:
        """INCR key，首次时设置过期时间，返回递增后的值。"""
        if not self._redis:
            return 0
        try:
            n = await self._redis.incr(key)
            if n == 1:
                await self._redis.expire(key, window_sec)
            return n
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
            self.logger.error("Failed to incr_with_expire %s: %s", key, e, exc_info=True)
            return 0
