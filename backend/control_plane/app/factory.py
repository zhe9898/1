from __future__ import annotations

import os
from collections.abc import Sequence
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.deps import get_settings
from backend.control_plane.app.exception_handlers import register_exception_handlers
from backend.control_plane.app.health import register_health_route
from backend.control_plane.app.lifespan import lifespan
from backend.control_plane.app.middleware_stack import register_request_middleware
from backend.control_plane.app.response_envelope import register_success_envelope
from backend.control_plane.app.router_admission import include_admitted_routers
from backend.kernel.contracts.runtime_version import get_runtime_version
from backend.middleware import RequestIDMiddleware

_is_production = os.getenv("ZEN70_ENV", "development").lower() == "production"

_API_STABILITY_TAGS: list[dict[str, object]] = [
    {"name": "auth", "description": "Authentication & token management", "x-stability": "stable"},
    {"name": "health", "description": "Health checks", "x-stability": "stable"},
    {"name": "jobs", "description": "Job lifecycle & dispatch", "x-stability": "stable"},
    {"name": "nodes", "description": "Node registration & status", "x-stability": "stable"},
    {"name": "connectors", "description": "Connector management", "x-stability": "stable"},
    {"name": "settings", "description": "System settings", "x-stability": "stable"},
    {"name": "console", "description": "Dashboard & operational views", "x-stability": "stable"},
    {"name": "workflows", "description": "Workflow orchestration", "x-stability": "beta"},
    {"name": "scheduling-governance", "description": "Scheduling policies, feature flags, decision audit", "x-stability": "beta"},
    {"name": "quotas", "description": "Tenant resource quotas", "x-stability": "beta"},
    {"name": "alerts", "description": "Alert rules & notifications", "x-stability": "beta"},
    {"name": "kernel", "description": "Kernel introspection & capabilities", "x-stability": "beta"},
    {"name": "extensions", "description": "Extension SDK manifests, published schemas, workflow templates", "x-stability": "beta"},
    {"name": "triggers", "description": "Unified trigger registry, ingress, and delivery history", "x-stability": "beta"},
    {"name": "reservations", "description": "Time-dimension reservations and backfill planning windows", "x-stability": "beta"},
    {"name": "node-approval", "description": "Node enrollment approval flow", "x-stability": "stable"},
    {"name": "audit-logs", "description": "Audit trail query", "x-stability": "stable"},
    {"name": "permissions", "description": "RBAC permission management", "x-stability": "stable"},
    {"name": "sessions", "description": "Session management", "x-stability": "stable"},
    {"name": "user-management", "description": "User admin", "x-stability": "stable"},
    {"name": "profile", "description": "User profile", "x-stability": "stable"},
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="ZEN70 API",
        version=get_runtime_version(),
        lifespan=lifespan,
        docs_url=None if _is_production else "/api/docs",
        redoc_url=None if _is_production else "/api/redoc",
        openapi_tags=_API_STABILITY_TAGS,
    )

    settings = get_settings()
    cors_origins = cast(Sequence[str], settings["cors_origins"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Idempotency-Key", "X-Requested-With"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )

    register_request_middleware(app)
    register_exception_handlers(app)
    register_health_route(app)
    include_admitted_routers(app)
    register_success_envelope(app)
    app.add_middleware(RequestIDMiddleware)
    return app
