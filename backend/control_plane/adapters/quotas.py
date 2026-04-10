"""Quota management API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_admin, get_current_user, get_tenant_db
from backend.models.quota import DEFAULT_QUOTAS
from backend.runtime.scheduling.quota_service import get_quota_status, set_quota

router = APIRouter(prefix="/api/v1/quotas", tags=["quotas"])


class QuotaSetRequest(BaseModel):
    resource_type: str = Field(..., description="nodes | connectors | jobs_concurrent | jobs_per_hour")
    limit: int = Field(..., ge=-1, description="-1 = unlimited")


class QuotaStatusItem(BaseModel):
    resource_type: str
    used: int
    limit: int
    pct: float


@router.get("", response_model=list[QuotaStatusItem])
async def get_quota_status_endpoint(
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[QuotaStatusItem]:
    """Get current quota usage for this tenant."""
    status = await get_quota_status(db, current_user["tenant_id"])
    items = []
    for resource_type, data in status.items():
        used, limit = data["used"], data["limit"]
        pct = round(used / limit * 100, 1) if limit > 0 else 0.0
        items.append(QuotaStatusItem(resource_type=resource_type, used=used, limit=limit, pct=pct))
    return items


@router.put("", response_model=QuotaStatusItem)
async def set_quota_endpoint(
    payload: QuotaSetRequest,
    current_user: dict[str, str] = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> QuotaStatusItem:
    """Set quota for tenant resource (admin only)."""
    valid_types = set(DEFAULT_QUOTAS.keys())
    if payload.resource_type not in valid_types:
        from backend.kernel.contracts.errors import zen

        raise zen("ZEN-QUOTA-4000", f"Unknown resource_type. Valid: {sorted(valid_types)}", status_code=400)

    quota = await set_quota(
        db,
        tenant_id=current_user["tenant_id"],
        resource_type=payload.resource_type,
        limit=payload.limit,
        updated_by=current_user["username"],
    )
    status = await get_quota_status(db, current_user["tenant_id"])
    data = status.get(payload.resource_type, {"used": 0, "limit": quota.limit})
    used, limit = data["used"], data["limit"]
    pct = round(used / limit * 100, 1) if limit > 0 else 0.0
    return QuotaStatusItem(resource_type=payload.resource_type, used=used, limit=limit, pct=pct)
