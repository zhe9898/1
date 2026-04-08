"""Console services for the backend-driven control plane."""

from .manifest_service import (
    get_control_plane_capability_keys,
    get_control_plane_route_names,
    iter_control_plane_surfaces,
)

__all__ = (
    "get_control_plane_capability_keys",
    "get_control_plane_route_names",
    "iter_control_plane_surfaces",
)
