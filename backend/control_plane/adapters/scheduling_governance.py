"""Scheduling Governance Admin API.

Consolidates all scheduling governance endpoints:
- Tenant scheduling policy CRUD
- Scheduling feature flags management
- Scheduling decision audit query
- Executor contract introspection
- Placement policy info
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control_plane.adapters.deps import get_current_admin, get_tenant_db
from backend.models.scheduling_decision import SchedulingDecision
from backend.models.tenant_scheduling_policy import TenantSchedulingPolicy
from backend.runtime.scheduling.placement_policy import get_placement_policy
from backend.runtime.scheduling.scheduling_governance import (
    SCHED_FLAG_DECISION_AUDIT,
    SCHED_FLAG_EXECUTOR_VALIDATION,
    SCHED_FLAG_GANG_SCHEDULING,
    SCHED_FLAG_PLACEMENT_POLICIES,
    SCHED_FLAG_PREEMPTION,
    SCHED_FLAG_PRIORITY_INHERITANCE,
    SCHED_FLAG_TENANT_POLICY_DB,
    delete_tenant_policy,
    get_all_scheduling_flags,
    list_tenant_policies,
    set_scheduling_feature,
    upsert_tenant_policy,
)
from backend.runtime.topology.executor_registry import get_executor_registry

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling-governance"])


# ── Request / Response models ─────────────────────────────────────────


class TenantPolicyRequest(BaseModel):
    tenant_id: str
    service_class: str = "standard"
    max_jobs_per_round: int | None = None
    fair_share_weight: float | None = None
    priority_boost: int = 0
    max_concurrent_jobs: int = -1
    placement_policy: str = ""
    enabled: bool = True
    notes: str | None = None


class TenantPolicyResponse(BaseModel):
    tenant_id: str
    service_class: str
    max_jobs_per_round: int
    fair_share_weight: float
    priority_boost: int
    max_concurrent_jobs: int
    placement_policy: str
    enabled: bool
    notes: str | None
    config_version: int = 1
    updated_at: datetime.datetime | None
    updated_by: str | None


class FeatureFlagResponse(BaseModel):
    key: str
    enabled: bool


class FeatureFlagSetRequest(BaseModel):
    key: str
    enabled: bool


class DecisionSummaryResponse(BaseModel):
    id: int
    tenant_id: str
    node_id: str
    cycle_ts: datetime.datetime
    candidates_count: int
    selected_count: int
    preemptions_count: int
    placement_policy: str
    policy_rejections: int
    duration_ms: int
    placements_json: list[dict] = Field(default_factory=list)
    rejections_json: list[dict] = Field(default_factory=list)
    context_json: dict = Field(default_factory=dict)


class ExecutorContractResponse(BaseModel):
    name: str
    description: str
    supported_kinds: list[str]
    requires_gpu: bool
    min_memory_mb: int
    min_cpu_cores: int
    stability_tier: str


class PlacementPolicyInfoResponse(BaseModel):
    name: str
    order: int


class SchedulingOverviewResponse(BaseModel):
    tenant_policies_count: int
    feature_flags: dict[str, bool]
    executor_contracts: list[ExecutorContractResponse]
    placement_policies: list[PlacementPolicyInfoResponse]
    recent_decisions_count: int


# ── Helpers ─────────────────────────────────────────────────────────


def _policy_to_response(p: TenantSchedulingPolicy) -> TenantPolicyResponse:
    return TenantPolicyResponse(
        tenant_id=p.tenant_id,
        service_class=p.service_class,
        max_jobs_per_round=p.max_jobs_per_round,
        fair_share_weight=p.fair_share_weight,
        priority_boost=p.priority_boost,
        max_concurrent_jobs=p.max_concurrent_jobs,
        placement_policy=p.placement_policy,
        enabled=p.enabled,
        notes=p.notes,
        config_version=p.config_version,
        updated_at=p.updated_at,
        updated_by=p.updated_by,
    )


# ── Tenant policies ──────────────────────────────────────────────────


@router.get("/policies", response_model=list[TenantPolicyResponse])
async def list_policies(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[TenantPolicyResponse]:
    """List all tenant scheduling policies (admin only)."""
    policies = await list_tenant_policies(db)
    return [_policy_to_response(p) for p in policies]


@router.put("/policies", response_model=TenantPolicyResponse)
async def upsert_policy(
    payload: TenantPolicyRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> TenantPolicyResponse:
    """Create or update a tenant scheduling policy (admin only)."""
    policy = await upsert_tenant_policy(
        db,
        tenant_id=payload.tenant_id,
        service_class=payload.service_class,
        max_jobs_per_round=payload.max_jobs_per_round,
        fair_share_weight=payload.fair_share_weight,
        priority_boost=payload.priority_boost,
        max_concurrent_jobs=payload.max_concurrent_jobs,
        placement_policy=payload.placement_policy,
        enabled=payload.enabled,
        notes=payload.notes,
        updated_by=str(admin.get("username", "admin")),
    )
    return _policy_to_response(policy)


@router.delete("/policies/{tenant_id}")
async def remove_policy(
    tenant_id: str,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Delete a tenant scheduling policy (reverts to defaults)."""
    deleted = await delete_tenant_policy(db, tenant_id)
    return {"deleted": deleted, "tenant_id": tenant_id}


# ── Feature flags ─────────────────────────────────────────────────────


@router.get("/flags", response_model=list[FeatureFlagResponse])
async def list_scheduling_flags(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[FeatureFlagResponse]:
    """List all scheduling feature flags (admin only)."""
    flags = await get_all_scheduling_flags(db)
    return [FeatureFlagResponse(key=k, enabled=v) for k, v in sorted(flags.items())]


@router.put("/flags", response_model=FeatureFlagResponse)
async def set_flag(
    payload: FeatureFlagSetRequest,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> FeatureFlagResponse:
    """Set a scheduling feature flag (admin only)."""
    # Validate key
    valid_keys = {
        SCHED_FLAG_PLACEMENT_POLICIES,
        SCHED_FLAG_DECISION_AUDIT,
        SCHED_FLAG_EXECUTOR_VALIDATION,
        SCHED_FLAG_TENANT_POLICY_DB,
        SCHED_FLAG_PREEMPTION,
        SCHED_FLAG_GANG_SCHEDULING,
        SCHED_FLAG_PRIORITY_INHERITANCE,
    }
    if payload.key not in valid_keys:
        from backend.kernel.contracts.errors import zen

        raise zen("ZEN-SCHED-4000", f"Unknown flag key. Valid: {sorted(valid_keys)}", status_code=400)

    await set_scheduling_feature(
        db,
        payload.key,
        payload.enabled,
        updated_by=str(admin.get("username") or admin.get("sub") or "admin"),
    )
    return FeatureFlagResponse(key=payload.key, enabled=payload.enabled)


# ── Decision audit ────────────────────────────────────────────────────


@router.get("/decisions", response_model=list[DecisionSummaryResponse])
async def list_decisions(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
    node_id: str | None = Query(None),
    since: datetime.datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[DecisionSummaryResponse]:
    """Query scheduling decision audit trail (admin only)."""
    q = select(SchedulingDecision).order_by(SchedulingDecision.cycle_ts.desc())
    if node_id:
        q = q.where(SchedulingDecision.node_id == node_id)
    if since:
        q = q.where(SchedulingDecision.cycle_ts >= since)
    q = q.limit(limit)

    result = await db.execute(q)
    decisions = result.scalars().all()
    return [
        DecisionSummaryResponse(
            id=d.id,
            tenant_id=d.tenant_id,
            node_id=d.node_id,
            cycle_ts=d.cycle_ts,
            candidates_count=d.candidates_count,
            selected_count=d.selected_count,
            preemptions_count=d.preemptions_count,
            placement_policy=d.placement_policy,
            policy_rejections=d.policy_rejections,
            duration_ms=d.duration_ms,
            placements_json=d.placements_json,
            rejections_json=d.rejections_json,
            context_json=d.context_json,
        )
        for d in decisions
    ]


# ── Executor contracts ────────────────────────────────────────────────


@router.get("/executors", response_model=list[ExecutorContractResponse])
async def list_executor_contracts(
    admin: dict = Depends(get_current_admin),
) -> list[ExecutorContractResponse]:
    """List all registered executor contracts."""
    registry = get_executor_registry()
    contracts = registry.all_contracts()
    return [
        ExecutorContractResponse(
            name=c.name,
            description=c.description,
            supported_kinds=sorted(c.supported_kinds),
            requires_gpu=c.requires_gpu,
            min_memory_mb=c.min_memory_mb,
            min_cpu_cores=c.min_cpu_cores,
            stability_tier=c.stability_tier,
        )
        for c in sorted(contracts.values(), key=lambda x: x.name)
    ]


# ── Placement policies ───────────────────────────────────────────────


@router.get("/placement-policies", response_model=list[PlacementPolicyInfoResponse])
async def list_placement_policies(
    admin: dict = Depends(get_current_admin),
) -> list[PlacementPolicyInfoResponse]:
    """List active placement policies."""
    composite = get_placement_policy()
    return [PlacementPolicyInfoResponse(name=p.name, order=p.order) for p in composite.policies]


# ── Overview ──────────────────────────────────────────────────────────


@router.get("/overview", response_model=SchedulingOverviewResponse)
async def scheduling_overview(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_tenant_db),
) -> SchedulingOverviewResponse:
    """High-level scheduling governance overview."""
    policies = await list_tenant_policies(db)
    flags = await get_all_scheduling_flags(db)

    one_hour_ago = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(hours=1)
    decisions_count_result = await db.execute(select(func.count()).where(SchedulingDecision.cycle_ts >= one_hour_ago))
    recent_decisions = decisions_count_result.scalar() or 0

    registry = get_executor_registry()
    contracts = registry.all_contracts()
    composite = get_placement_policy()

    return SchedulingOverviewResponse(
        tenant_policies_count=len(policies),
        feature_flags=flags,
        executor_contracts=[
            ExecutorContractResponse(
                name=c.name,
                description=c.description,
                supported_kinds=sorted(c.supported_kinds),
                requires_gpu=c.requires_gpu,
                min_memory_mb=c.min_memory_mb,
                min_cpu_cores=c.min_cpu_cores,
                stability_tier=c.stability_tier,
            )
            for c in sorted(contracts.values(), key=lambda x: x.name)
        ],
        placement_policies=[PlacementPolicyInfoResponse(name=p.name, order=p.order) for p in composite.policies],
        recent_decisions_count=recent_decisions,
    )
