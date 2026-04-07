from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.queue_stratification import SERVICE_CLASS_CONFIG, TenantQuota
from backend.models.tenant_scheduling_policy import TenantSchedulingPolicy


class SchedulingPolicyService:
    @staticmethod
    async def get(db: AsyncSession, tenant_id: str) -> TenantSchedulingPolicy | None:
        result = await db.execute(
            select(TenantSchedulingPolicy).where(TenantSchedulingPolicy.tenant_id == tenant_id)
        )
        return result.scalars().first()

    @staticmethod
    async def upsert(
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
        updated_by: str | None = None,
    ) -> TenantSchedulingPolicy:
        policy = await SchedulingPolicyService.get(db, tenant_id)
        sc_defaults = SERVICE_CLASS_CONFIG.get(service_class, SERVICE_CLASS_CONFIG["standard"])
        if policy is None:
            policy = TenantSchedulingPolicy(
                tenant_id=tenant_id,
                service_class=service_class,
                max_jobs_per_round=max_jobs_per_round or int(sc_defaults["max_jobs_per_round"]),
                fair_share_weight=fair_share_weight or float(sc_defaults["weight"]),
                priority_boost=priority_boost,
                max_concurrent_jobs=max_concurrent_jobs,
                placement_policy=placement_policy,
                enabled=enabled,
                notes=notes,
                updated_by=updated_by,
            )
            db.add(policy)
        else:
            policy.service_class = service_class
            if max_jobs_per_round is not None:
                policy.max_jobs_per_round = max_jobs_per_round
            if fair_share_weight is not None:
                policy.fair_share_weight = fair_share_weight
            policy.priority_boost = priority_boost
            policy.max_concurrent_jobs = max_concurrent_jobs
            policy.placement_policy = placement_policy
            policy.enabled = enabled
            policy.notes = notes
            policy.updated_by = updated_by
            policy.config_version = int(policy.config_version or 0) + 1
        await db.flush()
        return policy

    @staticmethod
    async def delete(db: AsyncSession, tenant_id: str) -> bool:
        policy = await SchedulingPolicyService.get(db, tenant_id)
        if policy is None:
            return False
        await db.delete(policy)
        await db.flush()
        return True

    @staticmethod
    async def list_all(db: AsyncSession) -> list[TenantSchedulingPolicy]:
        result = await db.execute(select(TenantSchedulingPolicy).order_by(TenantSchedulingPolicy.tenant_id))
        return list(result.scalars().all())

    @staticmethod
    def to_quota(policy: TenantSchedulingPolicy) -> TenantQuota:
        return TenantQuota(
            max_jobs_per_round=policy.max_jobs_per_round,
            weight=policy.fair_share_weight,
            service_class=policy.service_class,
        )
