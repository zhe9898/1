"""
能力矩阵模块：Redis 拓扑读取 → CapabilityItem 构建 → LRU 缓存 → FeatureFlag 双闸门。

红线合规：
- LRU 缓存 TTL ≤ 30s
- Redis 宕机使用 LRU 缓存兜底（悲观静默），恢复后清空脏缓存
- 冷启动 Redis 失联返回 ALL_OFF_MATRIX 并标记 bus_ready=False

A-1 修复：统一使用 app.state.redis (RedisClient 连接池)，消除 per-request 短连接。
A-2 修复：get_capabilities_matrix 拆解为 fetch_topology + _read_feature_flags + build_matrix。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field

from backend.core.control_plane import iter_control_plane_surfaces
from backend.core.errors import zen
from backend.core.gateway_profile import normalize_gateway_profile

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as _redis_mod
except ImportError:
    _redis_mod = None  # type: ignore[assignment]

_state_lock = asyncio.Lock()

# -------------------- 常量（法典 2.1.1）--------------------
TOPOLOGY_KEY_PREFIX = "zen70:topology:"
LRU_CACHE_TTL = 30


# -------------------- Pydantic 模型 --------------------
class CapabilityItem(BaseModel):
    """单个能力：status=online 则 enabled，PENDING_MAINTENANCE 则 disabled。"""

    status: str = Field(..., description="online | pending_maintenance | unknown")
    enabled: bool = Field(..., description="是否可交互")
    endpoint: str | None = Field(None, description="绑定的内网或外网端点")
    models: list[str] | None = Field(None, description="携带的具体模型数组")
    reason: str | None = Field(None, description="状态或异常附加说明")


# -------------------- 全局状态 --------------------
def build_public_capability_matrix(
    profile: str,
    *,
    is_admin: bool,
) -> dict[str, CapabilityItem]:
    """Public capability surface exposed to the kernel console."""
    runtime_profile = normalize_gateway_profile(profile)
    matrix: dict[str, CapabilityItem] = {}
    for surface in iter_control_plane_surfaces(runtime_profile, is_admin=is_admin):
        matrix[surface.capability_key] = CapabilityItem(
            status="online",
            enabled=True,
            endpoint=surface.endpoint,
            models=[runtime_profile],
            reason=surface.description,
        )
    return matrix


_lru_cache: dict[str, Any] = {}
_lru_ts: float = 0.0
_redis_available: bool = True

ALL_OFF_MATRIX: dict[str, CapabilityItem] = {
    "ups": CapabilityItem(status="offline", enabled=False, endpoint=None, models=None, reason="总线未就绪"),
    "network": CapabilityItem(status="offline", enabled=False, endpoint=None, models=None, reason="总线未就绪"),
    "gpu": CapabilityItem(status="offline", enabled=False, endpoint=None, models=None, reason="总线未就绪"),
}


# ==================== A-1: Redis 连接获取 ====================


def _get_redis_from_app(request: Request) -> Any:
    """从 app.state.redis 获取持久连接池 RedisClient，无则返回 None。"""
    app_redis = getattr(request.app.state, "redis", None)
    if app_redis is None:
        return None
    # RedisClient.redis 属性返回底层 redis.asyncio.Redis 实例
    return getattr(app_redis, "redis", None)


def is_redis_available() -> bool:
    """公有访问器：Redis 模块是否可用（已安装且可 import）。"""
    return _redis_mod is not None


# ==================== A-2: 拆解后的纯函数 ====================


async def fetch_topology(r: Any) -> dict[str, str]:
    """从 Redis 读取 zen70:topology:* 探针写入的状态（pipeline 批量读取）。纯 I/O。

    NOTE: Topology probes are reserved for pack-specific capabilities.
    Default kernel does not expose topology-based capabilities.
    """
    try:
        keys = [key async for key in r.scan_iter(f"{TOPOLOGY_KEY_PREFIX}*", count=100)]
        if not keys:
            return {}
        pipe = r.pipeline()
        for key in keys:
            pipe.get(key)
        values = await pipe.execute()
        result: dict[str, str] = {}
        for key, val in zip(keys, values):
            cap = key.replace(TOPOLOGY_KEY_PREFIX, "")
            result[cap] = (val or "unknown").strip()
        return result
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.debug("fetch_topology: %s", e)
        return {}


async def _read_feature_flags(r: Any | None) -> dict[str, str | None]:
    """从 Redis 批量读取 FeatureFlag 值。纯 I/O。

    NOTE: Feature flags are reserved for pack-specific capabilities.
    Default kernel does not use feature flags.
    """
    flags: dict[str, str | None] = {}
    if r is None:
        return flags
    try:
        # Reserved for future pack-specific feature flags
        pass
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.debug("feature flag cache read failed: %s", e)
    return flags


def _ff_to_bool(v: str | None) -> bool | None:
    """FeatureFlag 字符串 → bool | None 转换。

    NOTE: Reserved for pack-specific feature flags.
    """
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def build_matrix(
    topology: dict[str, str],
    feature_flags: dict[str, str | None],
) -> dict[str, CapabilityItem]:
    """纯转换函数：拓扑 + FeatureFlag → 能力矩阵。无 I/O，可直接单元测试。

    Kernel-only surface: Only Gateway control-plane capabilities are exposed.
    Business/Ops/AI capabilities must be delivered via explicit pack selection.
    """
    matrix: dict[str, CapabilityItem] = {}

    # Gateway 控制面入口（用于前端按协议动态渲染导航和首页卡片）
    matrix["Gateway Dashboard"] = CapabilityItem(
        status="online",
        enabled=True,
        endpoint="/v1/capabilities",
        models=["gateway-kernel"],
        reason="Control-plane entry",
    )
    matrix["Gateway Nodes"] = CapabilityItem(
        status="online",
        enabled=True,
        endpoint="/v1/nodes",
        models=["node-registry"],
        reason="Runner / sidecar registration and heartbeat",
    )
    matrix["Gateway Jobs"] = CapabilityItem(
        status="online",
        enabled=True,
        endpoint="/v1/jobs",
        models=["job-queue"],
        reason="Dispatch / pull / result / fail loop via Go Runner",
    )
    matrix["Gateway Connectors"] = CapabilityItem(
        status="online",
        enabled=True,
        endpoint="/v1/connectors",
        models=["connector-registry"],
        reason="Connector registration / invoke / test",
    )
    matrix["Gateway Settings"] = CapabilityItem(
        status="online",
        enabled=True,
        endpoint="/v1/settings/system",
        models=["runtime-config"],
        reason="Gateway runtime settings",
    )

    return matrix


# ==================== 胶水层（编排缓存 + I/O + 纯转换）====================


async def get_capabilities_matrix(request: Request) -> dict[str, CapabilityItem]:
    """
    获取能力矩阵。法典 2.1.1：LRU 缓存 TTL≤30s，Redis 宕机用缓存兜底（悲观静默），
    恢复后清空脏缓存重拉。

    A-1: 使用 app.state.redis 持久连接池（不再 per-request 新建+销毁连接）。
    A-2: 拆解为 fetch_topology() + _read_feature_flags() + build_matrix()。
    """
    global _lru_cache, _lru_ts, _redis_available
    now = time.time()

    r = _get_redis_from_app(request)
    if r is None:
        async with _state_lock:
            if now - _lru_ts <= LRU_CACHE_TTL and _lru_cache:
                request.state.bus_ready = True
                return _lru_cache.get("matrix", ALL_OFF_MATRIX)  # type: ignore[no-any-return]
        request.state.bus_ready = False
        return ALL_OFF_MATRIX

    try:
        topology = await fetch_topology(r)
        feature_flags = await _read_feature_flags(r)
        async with _state_lock:
            redis_was_down = not _redis_available
            _redis_available = True
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as e:
        logger.debug("get_capabilities_matrix Redis: %s", e)
        async with _state_lock:
            _redis_available = False
            if now - _lru_ts <= LRU_CACHE_TTL and _lru_cache:
                return _lru_cache.get("matrix", ALL_OFF_MATRIX)  # type: ignore[no-any-return]
        request.state.bus_ready = False
        return ALL_OFF_MATRIX

    matrix = build_matrix(topology, feature_flags)

    async with _state_lock:
        if redis_was_down and topology:
            _lru_cache.clear()
        _lru_cache = {"matrix": matrix, "topology_raw": topology}
        _lru_ts = now
    request.state.bus_ready = True
    return matrix


# ==================== 熔断与公有 API ====================


def check_capability_pending(matrix: dict[str, CapabilityItem], capability: str) -> bool:
    """若能力处于 PENDING_MAINTENANCE，返回 True（需熔断）。"""
    item = matrix.get(capability)
    if not item:
        return False
    return not item.enabled


def raise_503_if_pending(capability: str, matrix: dict[str, CapabilityItem]) -> None:
    """熔断大闸：PENDING_MAINTENANCE 时抛 503。"""
    if check_capability_pending(matrix, capability):
        raise zen(
            "ZEN-STOR-1001",
            "硬件维护中，请求已熔断",
            status_code=503,
            recovery_hint="请检查物理连接，探针重连后自动恢复。",
            details={"capability": capability},
        )


def clear_lru_cache() -> None:
    """供 lifespan shutdown 调用。"""
    _lru_cache.clear()


def get_lru_matrix() -> dict[str, CapabilityItem] | None:
    """公有访问器：返回 LRU 缓存中的能力矩阵，无缓存时返回 None。"""
    return _lru_cache.get("matrix")
