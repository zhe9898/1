from __future__ import annotations

from dataclasses import dataclass

from backend.kernel.capabilities.registry import get_capability


@dataclass(frozen=True, slots=True)
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
