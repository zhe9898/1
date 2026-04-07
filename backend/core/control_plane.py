from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.core.gateway_profile import get_enabled_router_names, normalize_gateway_profile
from backend.core.kernel_capabilities import get_capability
from backend.core.runtime_policy_resolver import get_runtime_policy_resolver


@dataclass(frozen=True)
class ControlPlaneSurface:
    surface_key: str
    capability_key: str
    route_name: str
    route_path: str
    label: str
    description: str
    endpoint: str
    backend_router: str
    frontend_view: str
    profiles: tuple[str, ...]
    required_scopes: tuple[str, ...] = ()
    pack_id: str = "zen70.core"
    policy_gates: tuple[str, ...] = ()
    requires_admin: bool = False

    @property
    def effective_scopes(self) -> tuple[str, ...]:
        capability = get_capability(self.capability_key)
        return self.required_scopes or (capability.scopes if capability is not None else ())


_KERNEL_CONTROL_PLANE_SURFACES: tuple[ControlPlaneSurface, ...] = (
    ControlPlaneSurface(
        surface_key="dashboard",
        capability_key="platform.capabilities.query",
        route_name="dashboard",
        route_path="/",
        label="Capabilities",
        description="Service capability matrix",
        endpoint="/v1/capabilities",
        backend_router="routes",
        frontend_view="CapabilitiesView",
        profiles=("gateway-kernel",),
        policy_gates=("router:routes",),
    ),
    ControlPlaneSurface(
        surface_key="nodes",
        capability_key="control.nodes.manage",
        route_name="nodes",
        route_path="/nodes",
        label="Nodes",
        description="Runner / sidecar registration and heartbeat",
        endpoint="/v1/nodes",
        backend_router="nodes",
        frontend_view="NodesView",
        profiles=("gateway-kernel",),
        policy_gates=("router:nodes",),
    ),
    ControlPlaneSurface(
        surface_key="jobs",
        capability_key="control.jobs.schedule",
        route_name="jobs",
        route_path="/jobs",
        label="Jobs",
        description="Dispatch / pull / result / fail loop via Go Runner",
        endpoint="/v1/jobs",
        backend_router="jobs",
        frontend_view="JobsView",
        profiles=("gateway-kernel",),
        policy_gates=("router:jobs",),
    ),
    ControlPlaneSurface(
        surface_key="connectors",
        capability_key="control.connectors.invoke",
        route_name="connectors",
        route_path="/connectors",
        label="Connectors",
        description="Connector registration / invoke / test",
        endpoint="/v1/connectors",
        backend_router="connectors",
        frontend_view="ConnectorsView",
        profiles=("gateway-kernel",),
        policy_gates=("router:connectors",),
    ),
    ControlPlaneSurface(
        surface_key="triggers",
        capability_key="control.triggers.manage",
        route_name="triggers",
        route_path="/triggers",
        label="Triggers",
        description="Unified trigger registry, webhook ingress, and delivery history",
        endpoint="/v1/triggers",
        backend_router="triggers",
        frontend_view="TriggersView",
        profiles=("gateway-kernel",),
        policy_gates=("router:triggers",),
    ),
    ControlPlaneSurface(
        surface_key="reservations",
        capability_key="control.reservations.manage",
        route_name="reservations",
        route_path="/reservations",
        label="Reservations",
        description="Time-dimension reservations, backfill windows, and planning diagnostics",
        endpoint="/v1/reservations",
        backend_router="reservations",
        frontend_view="ReservationsView",
        profiles=("gateway-kernel",),
        policy_gates=("router:reservations",),
    ),
    ControlPlaneSurface(
        surface_key="evaluations",
        capability_key="control.evaluations.manage",
        route_name="evaluations",
        route_path="/evaluations",
        label="Evaluations",
        description="Submit and review software evaluations across branches and components",
        endpoint="/v1/evaluations",
        backend_router="evaluations",
        frontend_view="EvaluationsView",
        profiles=("gateway-kernel",),
        policy_gates=("router:evaluations",),
    ),
    ControlPlaneSurface(
        surface_key="settings",
        capability_key="platform.settings.manage",
        route_name="settings",
        route_path="/settings",
        label="Settings",
        description="Gateway runtime settings",
        endpoint="/v1/settings/schema",
        backend_router="settings",
        frontend_view="SettingsView",
        profiles=("gateway-kernel",),
        policy_gates=("router:settings",),
        requires_admin=True,
    ),
)


def _validate_surface(surface: ControlPlaneSurface) -> None:
    capability = get_capability(surface.capability_key)
    if capability is None:
        raise ValueError(f"Surface '{surface.surface_key}' references unknown capability '{surface.capability_key}'")
    if surface.required_scopes and set(surface.required_scopes) != set(capability.scopes):
        raise ValueError(
            f"Surface '{surface.surface_key}' required scopes {surface.required_scopes} do not match capability scopes {capability.scopes}"
        )


@lru_cache(maxsize=1)
def load_control_plane_surfaces() -> tuple[ControlPlaneSurface, ...]:
    for surface in _KERNEL_CONTROL_PLANE_SURFACES:
        _validate_surface(surface)
    return _KERNEL_CONTROL_PLANE_SURFACES


def iter_control_plane_surfaces(
    profile: str,
    *,
    is_admin: bool,
    enabled_router_names: tuple[str, ...] | None = None,
) -> tuple[ControlPlaneSurface, ...]:
    normalized_profile = normalize_gateway_profile(profile)
    resolver = get_runtime_policy_resolver()
    enabled_routers = tuple(enabled_router_names or get_enabled_router_names(normalized_profile))
    visible: list[ControlPlaneSurface] = []
    for surface in load_control_plane_surfaces():
        if normalized_profile not in surface.profiles:
            continue
        if not resolver.router_enabled(surface.backend_router, profile=normalized_profile, enabled_router_names=enabled_routers):
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


def export_surface_registry() -> dict[str, dict[str, object]]:
    return {
        surface.surface_key: {
            "capability_key": surface.capability_key,
            "capability_keys": [surface.capability_key],
            "required_scope": list(surface.effective_scopes),
            "required_scopes": list(surface.effective_scopes),
            "pack_id": surface.pack_id,
            "policy_gate": list(surface.policy_gates),
            "policy_gates": list(surface.policy_gates),
            "route_name": surface.route_name,
            "route_path": surface.route_path,
            "endpoint": surface.endpoint,
        }
        for surface in load_control_plane_surfaces()
    }
