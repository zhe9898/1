from __future__ import annotations

import importlib
import os
from typing import Final

from fastapi import APIRouter, FastAPI

from backend.api import alerts as alerts_router
from backend.api import audit_logs as audit_logs_router
from backend.api import auth as auth_router
from backend.api import connectors as connectors_router
from backend.api import console as console_router
from backend.api import evaluations as evaluations_router
from backend.api import extensions as extensions_router
from backend.api import jobs as jobs_router
from backend.api import kernel as kernel_router
from backend.api import node_approval as node_approval_router
from backend.api import nodes as nodes_router
from backend.api import permissions as permissions_router
from backend.api import profile as profile_router
from backend.api import quotas as quotas_router
from backend.api import reservations as reservations_router
from backend.api import routes
from backend.api import scheduling_governance as scheduling_governance_router
from backend.api import sessions as sessions_router
from backend.api import settings as settings_router
from backend.api import triggers as triggers_router
from backend.api import user_management as user_management_router
from backend.api import workflows as workflows_router
from backend.kernel.packs.registry import get_pack_definition
from backend.kernel.profiles.public_profile import normalize_gateway_profile
from backend.kernel.topology.profile_selection import get_enabled_router_names as resolve_enabled_router_names
from backend.kernel.topology.profile_selection import (
    normalize_gateway_pack_keys,
)
from backend.platform.logging.structured import get_logger

logger = get_logger("api")

KERNEL_ALLOWED_OPTIONAL_ROUTERS: Final[frozenset[str]] = frozenset()

OPTIONAL_ROUTER_MODULES: Final[dict[str, str]] = {
    "assets": "backend.api.assets",
    "health": "backend.api.health",
    "portability": "backend.api.portability",
    "search": "backend.api.search",
}

CORE_ROUTER_REGISTRY: Final[dict[str, APIRouter]] = {
    "routes": routes.router,
    "auth": auth_router.router,
    "settings": settings_router.router,
    "profile": profile_router.router,
    "console": console_router.router,
    "nodes": nodes_router.router,
    "node_approval": node_approval_router.router,
    "jobs": jobs_router.router,
    "connectors": connectors_router.router,
    "triggers": triggers_router.router,
    "reservations": reservations_router.router,
    "extensions": extensions_router.router,
    "user_management": user_management_router.router,
    "audit_logs": audit_logs_router.router,
    "permissions": permissions_router.router,
    "sessions": sessions_router.router,
    "quotas": quotas_router.router,
    "alerts": alerts_router.router,
    "evaluations": evaluations_router.router,
    "kernel": kernel_router.router,
    "workflows": workflows_router.router,
    "scheduling_governance": scheduling_governance_router.router,
}


def get_gateway_profile() -> str:
    return normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))


def get_gateway_packs() -> tuple[str, ...]:
    return normalize_gateway_pack_keys(
        os.getenv("GATEWAY_PACKS", ""),
        profile=os.getenv("GATEWAY_PROFILE", "gateway-kernel"),
    )


def get_enabled_router_names(profile: str) -> tuple[str, ...]:
    raw_packs = os.getenv("GATEWAY_PACKS", "")
    selected_packs = normalize_gateway_pack_keys(raw_packs, profile=profile) if raw_packs else None
    return resolve_enabled_router_names(profile, selected_packs=selected_packs)


def _load_optional_router(name: str) -> APIRouter:
    module = importlib.import_module(OPTIONAL_ROUTER_MODULES[name])
    router = getattr(module, "router", None)
    if not isinstance(router, APIRouter):
        raise TypeError(f"Optional router '{name}' is missing a valid APIRouter")
    return router


def include_admitted_routers(app: FastAPI) -> None:
    for router_name, router_obj in CORE_ROUTER_REGISTRY.items():
        app.include_router(router_obj)
        logger.info("Loaded control-plane router: %s", router_name)

    gateway_profile = get_gateway_profile()
    gateway_packs = get_gateway_packs()
    pack_declared_routers: set[str] = set()
    for pack_key in gateway_packs:
        definition = get_pack_definition(pack_key)
        if definition is not None:
            pack_declared_routers.update(definition.routers)

    for router_name in get_enabled_router_names(gateway_profile):
        if router_name in CORE_ROUTER_REGISTRY:
            continue
        if router_name not in KERNEL_ALLOWED_OPTIONAL_ROUTERS and router_name not in pack_declared_routers:
            logger.info(
                "Skipping router '%s' (pack contract only, not admitted into the kernel runtime)",
                router_name,
            )
            continue
        if router_name not in OPTIONAL_ROUTER_MODULES:
            logger.info(
                "Router '%s' was admitted but no module is registered in OPTIONAL_ROUTER_MODULES",
                router_name,
            )
            continue
        app.include_router(_load_optional_router(router_name))
        logger.info("Loaded optional router: %s", router_name)
