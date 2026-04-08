"""Capability matrix helpers for the kernel control plane."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field

from backend.control_plane.console.manifest_service import iter_control_plane_surfaces
from backend.kernel.profiles.public_profile import normalize_gateway_profile

TOPOLOGY_KEY_PREFIX = "zen70:topology:"
LRU_CACHE_TTL = 30


class CapabilityItem(BaseModel):
    status: str = Field(..., description="online | pending_maintenance | offline | unknown")
    enabled: bool = Field(..., description="Whether the capability is available for use")
    endpoint: str | None = Field(default=None, description="Bound internal or external endpoint")
    models: list[str] | None = Field(default=None, description="Runtime profile or model hints")
    reason: str | None = Field(default=None, description="Human-readable status hint")


ALL_OFF_MATRIX: dict[str, CapabilityItem] = {
    "ups": CapabilityItem(status="offline", enabled=False, reason="control bus unavailable"),
    "network": CapabilityItem(status="offline", enabled=False, reason="control bus unavailable"),
    "gpu": CapabilityItem(status="offline", enabled=False, reason="control bus unavailable"),
}

_state_lock = asyncio.Lock()
_lru_cache: dict[str, CapabilityItem] | None = None
_lru_ts: float = 0.0


def clear_lru_cache() -> None:
    global _lru_cache, _lru_ts
    _lru_cache = None
    _lru_ts = 0.0


def get_lru_matrix() -> dict[str, CapabilityItem] | None:
    if _lru_cache is None:
        return None
    if time.time() - _lru_ts > LRU_CACHE_TTL:
        return None
    return dict(_lru_cache)


def _set_lru_matrix(matrix: dict[str, CapabilityItem]) -> None:
    global _lru_cache, _lru_ts
    _lru_cache = dict(matrix)
    _lru_ts = time.time()


def _get_redis_from_app(request: Request) -> Any:
    app_redis = getattr(request.app.state, "redis", None)
    if app_redis is None:
        return None
    return getattr(app_redis, "redis", None)


def is_redis_available() -> bool:
    try:
        import redis.asyncio  # noqa: F401
    except ImportError:
        return False
    return True


async def fetch_topology(redis_conn: Any) -> dict[str, str]:
    try:
        keys = [key async for key in redis_conn.scan_iter(f"{TOPOLOGY_KEY_PREFIX}*", count=100)]
        if not keys:
            return {}
        pipe = redis_conn.pipeline()
        for key in keys:
            pipe.get(key)
        values = await pipe.execute()
    except (OSError, ValueError, KeyError, RuntimeError, TypeError, AttributeError):
        return {}

    result: dict[str, str] = {}
    for key, value in zip(keys, values):
        capability = str(key).replace(TOPOLOGY_KEY_PREFIX, "")
        result[capability] = str(value or "unknown").strip() or "unknown"
    return result


async def _read_feature_flags(redis_conn: Any | None) -> dict[str, str | None]:
    del redis_conn
    return {}


def _ff_to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "off", "disabled"}:
        return False
    return None


def build_matrix(topology: dict[str, str], feature_flags: dict[str, str | None]) -> dict[str, CapabilityItem]:
    if not topology:
        return dict(ALL_OFF_MATRIX)

    matrix: dict[str, CapabilityItem] = {}
    for capability, status in topology.items():
        enabled_override = _ff_to_bool(feature_flags.get(capability))
        enabled = enabled_override if enabled_override is not None else status == "online"
        matrix[capability] = CapabilityItem(
            status=status,
            enabled=enabled,
            reason="redis topology probe",
        )
    return matrix


async def get_capabilities_matrix(request: Request) -> dict[str, CapabilityItem]:
    cached = get_lru_matrix()
    if cached is not None:
        return cached

    async with _state_lock:
        cached = get_lru_matrix()
        if cached is not None:
            return cached

        redis_conn = _get_redis_from_app(request)
        if redis_conn is None:
            matrix = dict(ALL_OFF_MATRIX)
            _set_lru_matrix(matrix)
            return matrix

        topology = await fetch_topology(redis_conn)
        feature_flags = await _read_feature_flags(redis_conn)
        matrix = build_matrix(topology, feature_flags)
        _set_lru_matrix(matrix)
        return matrix


def build_public_capability_matrix(
    profile: str,
    *,
    is_admin: bool,
) -> dict[str, CapabilityItem]:
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
