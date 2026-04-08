from __future__ import annotations

import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.api.deps import get_current_user_optional
from backend.api.ui_contracts import StatusView
from backend.control_plane.auth.access_policy import has_admin_role
from backend.control_plane.console.manifest_service import (
    get_control_plane_capability_keys,
    get_control_plane_route_names,
)
from backend.kernel.packs.registry import available_pack_definitions
from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.kernel.topology.profile_selection import (
    get_enabled_router_names,
    is_cluster_enabled,
    normalize_gateway_pack_keys,
    resolve_runtime_pack_keys,
)

router = APIRouter(prefix="/api/v1", tags=["profile"])


class GatewayPackSelectorResponse(BaseModel):
    required_capabilities: list[str] = Field(default_factory=list)
    target_zone: str | None = None
    target_executors: list[str] = Field(default_factory=list)


class GatewayPackResponse(BaseModel):
    pack_key: str
    label: str
    category: str
    description: str
    delivery_stage: str
    selected: bool = False
    inherited: bool = False
    services: list[str] = Field(default_factory=list)
    router_names: list[str] = Field(default_factory=list)
    capability_keys: list[str] = Field(default_factory=list)
    selector: GatewayPackSelectorResponse
    deployment_boundary: str
    runtime_owner: str
    status_view: StatusView


class GatewayProfileResponse(BaseModel):
    product: str = Field(..., description="Frozen product definition for default kernel line")
    profile: str = Field(..., description="Public profile name shown to console")
    runtime_profile: str | None = Field(default=None, description="Internal runtime profile used by backend (admin only)")
    router_names: list[str] = Field(default_factory=list)
    console_route_names: list[str] = Field(default_factory=list)
    capability_keys: list[str] = Field(default_factory=list)
    requested_pack_keys: list[str] = Field(default_factory=list)
    resolved_pack_keys: list[str] = Field(default_factory=list)
    packs: list[GatewayPackResponse] = Field(default_factory=list)
    cluster_enabled: bool = False


def _pack_status_view(*, selected: bool, inherited: bool) -> StatusView:
    if selected:
        return StatusView(key="selected", label="Selected", tone="success")
    if inherited:
        return StatusView(key="inherited", label="Inherited", tone="info")
    return StatusView(key="available", label="Available", tone="neutral")


def _build_pack_contracts(requested_pack_keys: tuple[str, ...], resolved_pack_keys: tuple[str, ...]) -> list[GatewayPackResponse]:
    requested_set = set(requested_pack_keys)
    resolved_set = set(resolved_pack_keys)
    contracts: list[GatewayPackResponse] = []
    for definition in available_pack_definitions():
        selected = definition.key in requested_set
        inherited = definition.key in resolved_set and not selected
        contracts.append(
            GatewayPackResponse(
                pack_key=definition.key,
                label=definition.label,
                category=definition.category,
                description=definition.description,
                delivery_stage=definition.delivery_stage,
                selected=selected,
                inherited=inherited,
                services=list(definition.services),
                router_names=list(definition.routers),
                capability_keys=list(definition.capability_keys),
                selector=GatewayPackSelectorResponse(
                    required_capabilities=list(definition.selector.required_capabilities),
                    target_zone=definition.selector.target_zone,
                    target_executors=list(definition.selector.target_executors),
                ),
                deployment_boundary=definition.deployment_boundary,
                runtime_owner=definition.runtime_owner,
                status_view=_pack_status_view(selected=selected, inherited=inherited),
            )
        )
    return contracts


@router.get("/profile", response_model=GatewayProfileResponse)
async def get_profile(
    current_user: dict | None = Depends(get_current_user_optional),
) -> GatewayProfileResponse:
    raw_profile = os.getenv("GATEWAY_PROFILE", "gateway-kernel")
    raw_packs = os.getenv("GATEWAY_PACKS", "")
    runtime_profile = normalize_gateway_profile(raw_profile)
    requested_pack_keys = normalize_gateway_pack_keys(raw_packs, profile=raw_profile)
    resolved_pack_keys = resolve_runtime_pack_keys(profile=raw_profile, raw_packs=raw_packs)
    is_admin = has_admin_role(current_user)
    return GatewayProfileResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile if is_admin else None,
        router_names=list(get_enabled_router_names(runtime_profile, selected_packs=requested_pack_keys)),
        console_route_names=list(get_control_plane_route_names(runtime_profile, is_admin=is_admin)),
        capability_keys=list(get_control_plane_capability_keys(runtime_profile, is_admin=is_admin)),
        requested_pack_keys=list(requested_pack_keys),
        resolved_pack_keys=list(resolved_pack_keys),
        packs=_build_pack_contracts(requested_pack_keys, resolved_pack_keys),
        cluster_enabled=is_cluster_enabled(runtime_profile, selected_packs=requested_pack_keys),
    )
