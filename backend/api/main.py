"""Thin FastAPI entrypoint for the backend-driven control plane."""

from __future__ import annotations

from backend.control_plane.app.factory import create_app
from backend.control_plane.app.router_admission import (
    KERNEL_ALLOWED_OPTIONAL_ROUTERS,
    OPTIONAL_ROUTER_MODULES,
    get_enabled_router_names,
    get_gateway_packs,
    get_gateway_profile,
)

app = create_app()

__all__ = (
    "KERNEL_ALLOWED_OPTIONAL_ROUTERS",
    "OPTIONAL_ROUTER_MODULES",
    "app",
    "get_enabled_router_names",
    "get_gateway_packs",
    "get_gateway_profile",
)
