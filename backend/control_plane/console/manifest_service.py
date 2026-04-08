from __future__ import annotations

from backend.kernel.policy.runtime_policy_resolver import get_runtime_policy_resolver
from backend.kernel.profiles.public_profile import normalize_gateway_profile
from backend.kernel.surfaces.contracts import ControlPlaneSurface
from backend.kernel.surfaces.registry import load_control_plane_surfaces
from backend.kernel.topology.profile_selection import get_enabled_router_names


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
        if not resolver.router_enabled(
            surface.backend_router,
            profile=normalized_profile,
            enabled_router_names=enabled_routers,
        ):
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
