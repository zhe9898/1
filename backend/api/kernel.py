"""Kernel capabilities discovery API.

GET /api/v1/kernel/capabilities  - list all kernel capability contracts
GET /api/v1/kernel/capabilities/{key} - get single capability
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.core.kernel_capabilities import (
    KernelCapability,
    get_capability,
    list_capabilities,
)

router = APIRouter(prefix="/api/v1/kernel", tags=["kernel"])


class CapabilityDetail(BaseModel):
    key: str
    version: str
    description: str
    endpoints: list[str]
    scopes: list[str]
    stable: bool


def _to_response(cap: KernelCapability) -> CapabilityDetail:
    return CapabilityDetail(
        key=cap.key,
        version=cap.version,
        description=cap.description,
        endpoints=list(cap.endpoints),
        scopes=list(cap.scopes),
        stable=cap.stable,
    )


@router.get("/capabilities", response_model=list[CapabilityDetail])
async def list_kernel_capabilities(
    stable_only: bool = False,
) -> list[CapabilityDetail]:
    """List all kernel capability contracts.

    Packs and business services use this to discover what the
    gateway kernel provides and what scopes are required.

    Stable capabilities are guaranteed not to break across minor versions.
    """
    return [_to_response(c) for c in list_capabilities(stable_only=stable_only)]


@router.get("/capabilities/{key:path}", response_model=CapabilityDetail)
async def get_kernel_capability(key: str) -> CapabilityDetail:
    """Get a single capability contract by key."""
    from backend.core.errors import zen

    cap = get_capability(key)
    if cap is None:
        raise zen("ZEN-KERNEL-4040", f"Capability '{key}' not found", status_code=404)
    return _to_response(cap)
