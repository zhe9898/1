"""Quota-Aware Fair-Share Scheduling — kernel-level resource quota enforcement.

Bridges the gap between platform-layer quota checks (backend/core/quota.py)
and the scheduling kernel.  Mature schedulers (Slurm, Nomad, YARN) enforce
quotas *inside* the scheduling loop, not just at job submission.

Capabilities:
1. **ResourceQuotaAccount** — per-tenant resource accounting (CPU, memory,
   GPU, concurrent jobs) tracked during each dispatch cycle.
2. **FairShareCalculator** — DRF (Dominant Resource Fairness) inspired
   allocation that considers *resource usage* not just job count.
3. **QuotaAwareGate** — SchedulingConstraint that rejects jobs whose
   tenant has exceeded any resource dimension quota.
4. **FairShareScoreModifier** — soft constraint that penalises tenants
   consuming above their fair share, boosting under-served tenants.

References:
- Slurm: Multifactor Priority + FairTree
- Nomad: namespace quotas (CPU/memory limits)
- YARN: DominantResourceFairnessPolicy
- K8s: ResourceQuota + LimitRange
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.models.job import Job

from backend.core.scheduling_constraints import SchedulingConstraint, SchedulingContext

logger = logging.getLogger(__name__)


# =====================================================================
# 1. Resource Quota Account — per-tenant resource tracking
# =====================================================================


@dataclass(slots=True)
class ResourceUsage:
    """Aggregate resource consumption for a single tenant."""

    cpu_cores: float = 0.0
    memory_mb: float = 0.0
    gpu_vram_mb: float = 0.0
    concurrent_jobs: int = 0


@dataclass(slots=True)
class ResourceQuotaLimit:
    """Hard limits for a tenant's resource consumption.

    A value of -1 means unlimited for that dimension.
    """

    max_cpu_cores: float = -1.0
    max_memory_mb: float = -1.0
    max_gpu_vram_mb: float = -1.0
    max_concurrent_jobs: int = -1

    def is_unlimited(self) -> bool:
        return all(
            v == -1
            for v in (
                self.max_cpu_cores,
                self.max_memory_mb,
                self.max_gpu_vram_mb,
                self.max_concurrent_jobs,
            )
        )


@dataclass
class ResourceQuotaAccount:
    """Tracks per-tenant resource consumption during a dispatch cycle.

    The account is populated at the start of each dispatch round from
    the current state of leased jobs, then updated as new placements
    are made within the cycle.
    """

    tenant_id: str
    usage: ResourceUsage = field(default_factory=ResourceUsage)
    limit: ResourceQuotaLimit = field(default_factory=ResourceQuotaLimit)

    def would_exceed(self, job: Job) -> tuple[bool, str]:
        """Check if placing this job would exceed any quota dimension."""
        lim = self.limit
        if lim.is_unlimited():
            return False, ""

        req_cpu = max(float(getattr(job, "required_cpu_cores", 0) or 0), 0.0)
        req_mem = max(float(getattr(job, "required_memory_mb", 0) or 0), 0.0)
        req_gpu = max(float(getattr(job, "required_gpu_vram_mb", 0) or 0), 0.0)

        if lim.max_cpu_cores >= 0 and self.usage.cpu_cores + req_cpu > lim.max_cpu_cores:
            return True, f"cpu_quota:{self.usage.cpu_cores + req_cpu:.1f}/{lim.max_cpu_cores:.1f}"
        if lim.max_memory_mb >= 0 and self.usage.memory_mb + req_mem > lim.max_memory_mb:
            return True, f"memory_quota:{self.usage.memory_mb + req_mem:.0f}/{lim.max_memory_mb:.0f}"
        if lim.max_gpu_vram_mb >= 0 and self.usage.gpu_vram_mb + req_gpu > lim.max_gpu_vram_mb:
            return True, f"gpu_quota:{self.usage.gpu_vram_mb + req_gpu:.0f}/{lim.max_gpu_vram_mb:.0f}"
        if lim.max_concurrent_jobs >= 0 and self.usage.concurrent_jobs + 1 > lim.max_concurrent_jobs:
            return True, f"job_quota:{self.usage.concurrent_jobs + 1}/{lim.max_concurrent_jobs}"

        return False, ""

    def record_placement(self, job: Job) -> None:
        """Account for a newly placed job."""
        self.usage.cpu_cores += max(float(getattr(job, "required_cpu_cores", 0) or 0), 0.0)
        self.usage.memory_mb += max(float(getattr(job, "required_memory_mb", 0) or 0), 0.0)
        self.usage.gpu_vram_mb += max(float(getattr(job, "required_gpu_vram_mb", 0) or 0), 0.0)
        self.usage.concurrent_jobs += 1


# =====================================================================
# 2. Fair-Share Calculator — DRF-inspired allocation
# =====================================================================


class FairShareCalculator:
    """Compute per-tenant fair-share ratios using Dominant Resource Fairness.

    DRF ensures that each tenant's dominant resource share (the dimension
    where they consume the largest fraction of their quota or the cluster)
    is equalised, weighted by their service class weight.

    Returns a ``fair_share_ratio`` per tenant:
    - < 1.0 → tenant is under-served (should be boosted)
    - = 1.0 → tenant is at fair share
    - > 1.0 → tenant is over-served (should be penalised)
    """

    @staticmethod
    def compute_fair_shares(
        accounts: dict[str, ResourceQuotaAccount],
        cluster_totals: ResourceUsage,
    ) -> dict[str, float]:
        """Return {tenant_id: fair_share_ratio}."""
        if not accounts:
            return {}

        total_weight = 0.0
        tenant_weights: dict[str, float] = {}
        for tid, acct in accounts.items():
            # Weight is read from the scheduling context
            w = getattr(acct, "_weight", 1.0)
            tenant_weights[tid] = w
            total_weight += w

        if total_weight <= 0:
            total_weight = len(accounts)
            tenant_weights = {t: 1.0 for t in accounts}

        ratios: dict[str, float] = {}
        for tid, acct in accounts.items():
            ideal_share = tenant_weights[tid] / total_weight
            dominant = _dominant_resource_share(acct.usage, cluster_totals)
            if ideal_share > 0:
                ratios[tid] = dominant / ideal_share
            else:
                ratios[tid] = 0.0

        return ratios


def _dominant_resource_share(usage: ResourceUsage, total: ResourceUsage) -> float:
    """Return the max share across resource dimensions."""
    shares: list[float] = []
    if total.cpu_cores > 0:
        shares.append(usage.cpu_cores / total.cpu_cores)
    if total.memory_mb > 0:
        shares.append(usage.memory_mb / total.memory_mb)
    if total.gpu_vram_mb > 0:
        shares.append(usage.gpu_vram_mb / total.gpu_vram_mb)
    if total.concurrent_jobs > 0:
        shares.append(usage.concurrent_jobs / total.concurrent_jobs)
    return max(shares) if shares else 0.0


# =====================================================================
# 3. QuotaAwareGate — hard constraint in the scheduling pipeline
# =====================================================================


class QuotaAwareGate(SchedulingConstraint):
    """Hard gate: reject jobs whose tenant has exceeded resource quotas.

    Reads ``ctx.data["_quota_accounts"]`` populated by the dispatch cycle.
    If not present, passes all jobs (backward-compatible).
    """

    name = "resource_quota"
    order = 5  # Very early — before fair-share, deps, gangs
    hard = True

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        accounts: dict[str, ResourceQuotaAccount] | None = ctx.data.get("_quota_accounts")  # type: ignore[assignment]
        if accounts is None:
            return True, ""

        tenant_id = getattr(job, "tenant_id", "default")
        acct = accounts.get(tenant_id)
        if acct is None:
            return True, ""

        exceeded, reason = acct.would_exceed(job)
        if exceeded:
            return False, f"resource_quota_exceeded:{reason}"
        return True, ""


# =====================================================================
# 4. FairShareScoreModifier — soft penalty/boost based on fair-share
# =====================================================================


# Maximum score adjustment (positive or negative) — read from policy store
def _get_fair_share_config() -> tuple[int, float, int]:
    """Return (max_adjustment, deadband, priority_cap) from the policy store."""
    try:
        from backend.core.scheduling_policy_store import get_policy_store

        fs = get_policy_store().active.fair_share
        return fs.max_score_adjustment, fs.deadband, fs.priority_cap
    except Exception:
        return 40, 0.05, 160


class FairShareScoreModifier(SchedulingConstraint):
    """Soft modifier: boost under-served tenants, penalise over-served ones.

    Reads ``ctx.data["_fair_share_ratios"]`` (populated by dispatch cycle)
    and adjusts job priority accordingly.

    Score adjustment = clamp(-40, +40, -40 × (ratio - 1.0))
    - ratio < 1.0 → positive boost (under-served)
    - ratio = 1.0 → no change
    - ratio > 1.0 → negative penalty (over-served)
    """

    name = "fair_share_modifier"
    order = 7  # After resource_quota (5), before tenant_fair_share (8)
    hard = False

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        ratios: dict[str, float] | None = ctx.data.get("_fair_share_ratios")  # type: ignore[assignment]
        if ratios is None:
            return True, ""

        tenant_id = getattr(job, "tenant_id", "default")
        ratio = ratios.get(tenant_id, 1.0)

        max_adj, deadband, priority_cap = _get_fair_share_config()

        if abs(ratio - 1.0) < deadband:
            return True, ""

        adjustment = max(-max_adj, min(max_adj, int(-max_adj * (ratio - 1.0))))
        current_pri = int(job.priority or 50)
        job.priority = max(0, min(priority_cap, current_pri + adjustment))
        return True, f"fair_share_adj:{adjustment}:ratio:{ratio:.2f}"


# =====================================================================
# 5. Quota configuration loader
# =====================================================================


_DEFAULT_RESOURCE_QUOTAS: dict[str, ResourceQuotaLimit] = {}


def load_resource_quotas() -> dict[str, ResourceQuotaLimit]:
    """Load per-tenant resource quota limits from system.yaml.

    Config example::

        scheduling:
          resource_quotas:
            tenant-alpha:
              max_cpu_cores: 64
              max_memory_mb: 131072
              max_gpu_vram_mb: 49152
              max_concurrent_jobs: 100
            tenant-beta:
              max_cpu_cores: 32
              max_concurrent_jobs: 50
    """
    try:
        from pathlib import Path

        import yaml

        config = yaml.safe_load(Path("system.yaml").read_text(encoding="utf-8"))
        sched = config.get("scheduling", {}) or {}
        raw = sched.get("resource_quotas", {}) or {}
        quotas: dict[str, ResourceQuotaLimit] = {}
        for tenant_id, cfg in raw.items():
            if isinstance(cfg, dict):
                quotas[tenant_id] = ResourceQuotaLimit(
                    max_cpu_cores=float(cfg.get("max_cpu_cores", -1)),
                    max_memory_mb=float(cfg.get("max_memory_mb", -1)),
                    max_gpu_vram_mb=float(cfg.get("max_gpu_vram_mb", -1)),
                    max_concurrent_jobs=int(cfg.get("max_concurrent_jobs", -1)),
                )
        return quotas
    except Exception:
        return dict(_DEFAULT_RESOURCE_QUOTAS)


def build_quota_accounts(
    leased_jobs: list[Job],
    quotas: dict[str, ResourceQuotaLimit] | None = None,
) -> dict[str, ResourceQuotaAccount]:
    """Build per-tenant quota accounts from currently leased jobs.

    Called at the start of each dispatch cycle to snapshot current usage.
    """
    if quotas is None:
        quotas = load_resource_quotas()

    accounts: dict[str, ResourceQuotaAccount] = {}
    for job in leased_jobs:
        tid = getattr(job, "tenant_id", "default")
        if tid not in accounts:
            limit = quotas.get(tid, ResourceQuotaLimit())
            accounts[tid] = ResourceQuotaAccount(tenant_id=tid, limit=limit)
        acct = accounts[tid]
        acct.usage.cpu_cores += max(float(getattr(job, "required_cpu_cores", 0) or 0), 0.0)
        acct.usage.memory_mb += max(float(getattr(job, "required_memory_mb", 0) or 0), 0.0)
        acct.usage.gpu_vram_mb += max(float(getattr(job, "required_gpu_vram_mb", 0) or 0), 0.0)
        acct.usage.concurrent_jobs += 1

    # Ensure all tenants with quotas have accounts even if no active jobs
    for tid, limit in quotas.items():
        if tid not in accounts:
            accounts[tid] = ResourceQuotaAccount(tenant_id=tid, limit=limit)

    return accounts
