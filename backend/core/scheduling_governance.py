"""Scheduling Governance — DB-first tenant policy + decision audit logger.

Upgrades GlobalFairScheduler to read from DB (with YAML seed/fallback),
provides feature-flag guards for scheduling capabilities, and houses
the ``SchedulingDecisionLogger`` for dispatch audit trail.

**Module boundary**
This module contains the *implementations* that ``governance_facade.py``
delegates to:

- ``get_tenant_policy`` / ``upsert_tenant_policy`` — CRUD for per-tenant
  scheduling policies stored in ``TenantSchedulingPolicy``.
- ``SchedulingDecisionLogger`` — accumulates placed/rejected/preempted job
  records during a dispatch cycle and flushes them to ``SchedulingDecision``.
- ``is_scheduling_feature_enabled`` / ``set_scheduling_feature`` — DB-backed
  feature flags that gate capabilities such as placement policies, gang
  scheduling, and priority inheritance.

Do **not** call these functions directly from dispatch paths; use
``GovernanceFacade`` from ``governance_facade.py`` so that admission
control and sealing are always enforced.
"""

from __future__ import annotations

import datetime
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.feature_flag_service import FeatureFlagService
from backend.kernel.scheduling.queue_stratification import (
    TenantQuota,
)
from backend.core.scheduling_policy_service import SchedulingPolicyService
from backend.models.scheduling_decision import SchedulingDecision
from backend.models.tenant_scheduling_policy import TenantSchedulingPolicy

logger = logging.getLogger(__name__)

# =====================================================================
# 1. DB-backed tenant policy lookup
# =====================================================================


async def get_tenant_policy(
    db: AsyncSession,
    tenant_id: str,
) -> TenantSchedulingPolicy | None:
    return await SchedulingPolicyService.get(db, tenant_id)


async def upsert_tenant_policy(
    db: AsyncSession,
    *,
    tenant_id: str,
    service_class: str = "standard",
    max_jobs_per_round: int | None = None,
    fair_share_weight: float | None = None,
    priority_boost: int = 0,
    max_concurrent_jobs: int = -1,
    placement_policy: str = "",
    enabled: bool = True,
    notes: str | None = None,
    updated_by: str = "system",
) -> TenantSchedulingPolicy:
    return await SchedulingPolicyService.upsert(
        db,
        tenant_id=tenant_id,
        service_class=service_class,
        max_jobs_per_round=max_jobs_per_round,
        fair_share_weight=fair_share_weight,
        priority_boost=priority_boost,
        max_concurrent_jobs=max_concurrent_jobs,
        placement_policy=placement_policy,
        enabled=enabled,
        notes=notes,
        updated_by=updated_by,
    )


async def list_tenant_policies(db: AsyncSession) -> list[TenantSchedulingPolicy]:
    return await SchedulingPolicyService.list_all(db)


async def delete_tenant_policy(db: AsyncSession, tenant_id: str) -> bool:
    return await SchedulingPolicyService.delete(db, tenant_id)


def policy_to_quota(policy: TenantSchedulingPolicy) -> TenantQuota:
    return SchedulingPolicyService.to_quota(policy)


# =====================================================================
# 2. Scheduling decision audit logger
# =====================================================================


class SchedulingDecisionLogger:
    """Accumulates placement/rejection info during a dispatch cycle
    and writes a single ``SchedulingDecision`` row at commit time.
    """

    __slots__ = (
        "tenant_id",
        "node_id",
        "cycle_ts",
        "candidates_count",
        "placements",
        "rejections",
        "preemptions_count",
        "policy_rejections",
        "placement_policy_name",
        "context",
        "_start_ns",
    )

    def __init__(self, *, tenant_id: str, node_id: str, now: datetime.datetime) -> None:
        self.tenant_id = tenant_id
        self.node_id = node_id
        self.cycle_ts = now
        self.candidates_count = 0
        self.placements: list[dict] = []
        self.rejections: list[dict] = []
        self.preemptions_count = 0
        self.policy_rejections = 0
        self.placement_policy_name = "default"
        self.context: dict = {}
        self._start_ns = time.monotonic_ns()

    def record_placement(
        self,
        job_id: str,
        score: int,
        breakdown: dict[str, int] | None = None,
        eligible_nodes: int = 0,
    ) -> None:
        self.placements.append(
            {
                "job_id": job_id,
                "score": score,
                "breakdown": breakdown or {},
                "eligible_nodes": eligible_nodes,
            }
        )

    def record_rejection(self, job_id: str, reason: str) -> None:
        self.rejections.append({"job_id": job_id, "reason": reason})

    def record_preemption(self, victim_job_id: str, by_job_id: str, reason: str) -> None:
        self.preemptions_count += 1
        self.context.setdefault("preemptions", []).append(
            {
                "victim": victim_job_id,
                "by": by_job_id,
                "reason": reason,
            }
        )

    def record_policy_rejection(self, job_id: str, policy_name: str, reason: str) -> None:
        self.policy_rejections += 1
        self.rejections.append(
            {
                "job_id": job_id,
                "reason": f"policy:{policy_name}: {reason}",
            }
        )

    async def flush(self, db: AsyncSession) -> SchedulingDecision | None:
        """Write the accumulated decision record to DB.

        Returns the decision row (not yet committed — caller owns the tx).
        Skips write if no candidates were processed (empty pull cycle).
        """
        if self.candidates_count == 0 and not self.placements:
            return None

        elapsed_ms = int((time.monotonic_ns() - self._start_ns) / 1_000_000)
        decision = SchedulingDecision(
            tenant_id=self.tenant_id,
            node_id=self.node_id,
            cycle_ts=self.cycle_ts,
            candidates_count=self.candidates_count,
            selected_count=len(self.placements),
            preemptions_count=self.preemptions_count,
            placement_policy=self.placement_policy_name,
            policy_rejections=self.policy_rejections,
            placements_json=self.placements,
            rejections_json=self.rejections[-50:],  # cap to avoid huge rows
            duration_ms=elapsed_ms,
            context_json=self.context,
        )
        db.add(decision)
        await db.flush()
        return decision


# =====================================================================
# 3. Scheduling feature flags (works with existing FeatureFlag model)
# =====================================================================

# Canonical flag keys for scheduling capabilities
SCHED_FLAG_PLACEMENT_POLICIES = "sched_placement_policies"
SCHED_FLAG_DECISION_AUDIT = "sched_decision_audit"
SCHED_FLAG_EXECUTOR_VALIDATION = "sched_executor_validation"
SCHED_FLAG_TENANT_POLICY_DB = "sched_tenant_policy_db"
SCHED_FLAG_PREEMPTION = "sched_preemption"
SCHED_FLAG_GANG_SCHEDULING = "sched_gang_scheduling"
SCHED_FLAG_PRIORITY_INHERITANCE = "sched_priority_inheritance"

# Default states: conservative — all new capabilities start disabled
_SCHEDULING_FLAG_DEFAULTS: dict[str, bool] = {
    SCHED_FLAG_PLACEMENT_POLICIES: True,
    SCHED_FLAG_DECISION_AUDIT: True,
    SCHED_FLAG_EXECUTOR_VALIDATION: False,
    SCHED_FLAG_TENANT_POLICY_DB: False,
    SCHED_FLAG_PREEMPTION: True,
    SCHED_FLAG_GANG_SCHEDULING: True,
    SCHED_FLAG_PRIORITY_INHERITANCE: True,
}


async def is_scheduling_feature_enabled(
    db: AsyncSession,
    flag_key: str,
) -> bool:
    """Check whether a scheduling feature flag is enabled.

    Falls back to ``_SCHEDULING_FLAG_DEFAULTS`` if the flag is not in DB.
    """
    from backend.models.feature_flag import FeatureFlag

    result = await db.execute(select(FeatureFlag).where(FeatureFlag.key == flag_key))
    flag = result.scalars().first()
    if flag is not None:
        return bool(flag.enabled)
    return _SCHEDULING_FLAG_DEFAULTS.get(flag_key, False)


async def set_scheduling_feature(
    db: AsyncSession,
    flag_key: str,
    enabled: bool,
    *,
    updated_by: str | None = None,
) -> None:
    await FeatureFlagService.set_flag(
        db,
        key=flag_key,
        enabled=enabled,
        description=f"Scheduling feature: {flag_key}",
        category="scheduling",
        updated_by=updated_by,
    )


async def get_all_scheduling_flags(db: AsyncSession) -> dict[str, bool]:
    """Return current state of all scheduling feature flags."""
    from backend.models.feature_flag import FeatureFlag

    result = await db.execute(select(FeatureFlag).where(FeatureFlag.category == "scheduling"))
    db_flags = {f.key: bool(f.enabled) for f in result.scalars().all()}
    # Merge with defaults
    merged = dict(_SCHEDULING_FLAG_DEFAULTS)
    merged.update(db_flags)
    return merged
