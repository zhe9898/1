from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.core.gateway_profile import get_enabled_router_names, normalize_gateway_profile


@dataclass(frozen=True)
class ControlPlaneSurface:
    capability_key: str
    route_name: str
    route_path: str
    label: str
    description: str
    endpoint: str
    backend_router: str
    frontend_view: str
    profiles: tuple[str, ...]
    requires_admin: bool = False


# Kernel control-plane surfaces (backend is the single source of truth)
_KERNEL_CONTROL_PLANE_SURFACES: tuple[ControlPlaneSurface, ...] = (
    ControlPlaneSurface(
        capability_key="gateway.capabilities",
        route_name="dashboard",
        route_path="/",
        label="Capabilities",
        description="Service capability matrix",
        endpoint="/v1/capabilities",
        backend_router="routes",
        frontend_view="CapabilitiesView",
        profiles=("gateway-kernel",),
        requires_admin=False,
    ),
    ControlPlaneSurface(
        capability_key="gateway.nodes",
        route_name="nodes",
        route_path="/nodes",
        label="Nodes",
        description="Runner / sidecar registration and heartbeat",
        endpoint="/v1/nodes",
        backend_router="nodes",
        frontend_view="NodesView",
        profiles=("gateway-kernel",),
        requires_admin=False,
    ),
    ControlPlaneSurface(
        capability_key="gateway.jobs",
        route_name="jobs",
        route_path="/jobs",
        label="Jobs",
        description="Dispatch / pull / result / fail loop via Go Runner",
        endpoint="/v1/jobs",
        backend_router="jobs",
        frontend_view="JobsView",
        profiles=("gateway-kernel",),
        requires_admin=False,
    ),
    ControlPlaneSurface(
        capability_key="gateway.connectors",
        route_name="connectors",
        route_path="/connectors",
        label="Connectors",
        description="Connector registration / invoke / test",
        endpoint="/v1/connectors",
        backend_router="connectors",
        frontend_view="ConnectorsView",
        profiles=("gateway-kernel",),
        requires_admin=False,
    ),
    ControlPlaneSurface(
        capability_key="gateway.triggers",
        route_name="triggers",
        route_path="/triggers",
        label="Triggers",
        description="Unified trigger registry, webhook ingress, and delivery history",
        endpoint="/v1/triggers",
        backend_router="triggers",
        frontend_view="TriggersView",
        profiles=("gateway-kernel",),
        requires_admin=False,
    ),
    ControlPlaneSurface(
        capability_key="gateway.settings",
        route_name="settings",
        route_path="/settings",
        label="Settings",
        description="Gateway runtime settings",
        endpoint="/v1/settings/schema",
        backend_router="settings",
        frontend_view="SettingsView",
        profiles=("gateway-kernel",),
        requires_admin=True,
    ),
)


@lru_cache(maxsize=1)
def load_control_plane_surfaces() -> tuple[ControlPlaneSurface, ...]:
    """Load kernel control-plane surfaces.

    Backend is the single source of truth for control-plane surfaces.
    Frontend should fetch surfaces from /api/v1/console/surfaces.
    """
    return _KERNEL_CONTROL_PLANE_SURFACES


def iter_control_plane_surfaces(
    profile: str,
    *,
    is_admin: bool,
    enabled_router_names: tuple[str, ...] | None = None,
) -> tuple[ControlPlaneSurface, ...]:
    normalized_profile = normalize_gateway_profile(profile)
    enabled_routers = set(enabled_router_names or get_enabled_router_names(normalized_profile))
    visible: list[ControlPlaneSurface] = []
    for surface in load_control_plane_surfaces():
        if normalized_profile not in surface.profiles:
            continue
        if surface.backend_router not in enabled_routers:
            continue
        if surface.requires_admin and not is_admin:
            continue
        visible.append(surface)
    return tuple(visible)


def get_control_plane_route_names(
    profile: str,
    *,
    is_admin: bool,
    enabled_router_names: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    return tuple(
        surface.route_name
        for surface in iter_control_plane_surfaces(
            profile,
            is_admin=is_admin,
            enabled_router_names=enabled_router_names,
        )
    )


def get_control_plane_capability_keys(
    profile: str,
    *,
    is_admin: bool,
    enabled_router_names: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    return tuple(
        surface.capability_key
        for surface in iter_control_plane_surfaces(
            profile,
            is_admin=is_admin,
            enabled_router_names=enabled_router_names,
        )
    )
