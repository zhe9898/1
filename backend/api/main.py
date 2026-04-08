"""Thin FastAPI entrypoint for the backend-driven control plane."""

from __future__ import annotations

import signal as signal
from contextlib import asynccontextmanager
from typing import Any

import backend.control_plane.app.health as _health_module
import backend.control_plane.app.lifespan as _lifespan_module

from backend.api.deps import get_settings
from backend.control_plane.app.entrypoint import app
from backend.control_plane.app.factory import _API_STABILITY_TAGS
from backend.control_plane.app.lifespan import check_postgres_async as _check_postgres_async
from backend.control_plane.app.response_envelope import success_envelope
from backend.control_plane.app.router_admission import (
    KERNEL_ALLOWED_OPTIONAL_ROUTERS,
    OPTIONAL_ROUTER_MODULES,
    get_enabled_router_names,
    get_gateway_packs,
    get_gateway_profile,
)

connect_redis_with_retry = _lifespan_module.connect_redis_with_retry


async def health_check(request: Any) -> Any:
    original_get_settings = _health_module.get_settings
    original_check_postgres = _health_module.check_postgres_async
    try:
        _health_module.get_settings = get_settings
        _health_module.check_postgres_async = _check_postgres_async
        return await _health_module.health_check(request)
    finally:
        _health_module.get_settings = original_get_settings
        _health_module.check_postgres_async = original_check_postgres


@asynccontextmanager
async def lifespan(app_instance: Any) -> object:
    original_connect_redis = _lifespan_module.connect_redis_with_retry
    original_signal = _lifespan_module.signal
    try:
        _lifespan_module.connect_redis_with_retry = connect_redis_with_retry
        _lifespan_module.signal = signal
        async with _lifespan_module.lifespan(app_instance):
            yield
    finally:
        _lifespan_module.connect_redis_with_retry = original_connect_redis
        _lifespan_module.signal = original_signal


__all__ = (
    "_API_STABILITY_TAGS",
    "_check_postgres_async",
    "KERNEL_ALLOWED_OPTIONAL_ROUTERS",
    "OPTIONAL_ROUTER_MODULES",
    "app",
    "connect_redis_with_retry",
    "get_enabled_router_names",
    "get_gateway_packs",
    "get_gateway_profile",
    "get_settings",
    "health_check",
    "lifespan",
    "signal",
    "success_envelope",
)
