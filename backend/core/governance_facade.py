"""Governance Facade — single mandatory entry for the dispatch chain.

All scheduling decisions MUST flow through ``GovernanceFacade`` so that:

1. Admission control is applied before any DB-heavy candidate query.
2. Executor contract validation is a first-class gate, not an inline ``if``.
3. Feature flags, constraint pipeline, placement policies, preemption budget,
   scheduling backoff, and decision audit are orchestrated from one place.
4. The facade can be **sealed** after boot, preventing runtime hot-patch of
   scheduling policies without explicit admin ``unseal``.

The dispatch.py ``pull_jobs`` function calls:
- ``facade.pre_dispatch_admission(...)`` → admission gate
- ``facade.filter_by_executor_contract(...)`` → kind-compat pre-filter
- ``facade.post_dispatch_audit(...)`` → decision audit flush
- ``facade.is_sealed`` → checked by ``set_scheduling_feature()`` to block mutations

All strategy selection also flows through ``facade.resolve_strategy(...)``.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class AdmissionResult:
    """Result of pre-dispatch admission check."""

    admitted: bool
    reason: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ExecutorFilterResult:
    """Result of executor contract filtering on a single job."""

    compatible: bool
    reason: str = ""


class GovernanceFacade:
    """Singleton governance entry point for the entire dispatch chain.

    Owns the lifecycle of all scheduling sub-systems and enforces that
    every dispatch cycle goes through a unified governance pipeline.
    """

    __slots__ = ("_sealed", "_seal_reason", "_metrics_enabled")

    def __init__(self) -> None:
        self._sealed: bool = False
        self._seal_reason: str = ""
        self._metrics_enabled: bool = True

    # ── Seal / Unseal ────────────────────────────────────────────────

    @property
    def is_sealed(self) -> bool:
        return self._sealed

    @property
    def seal_reason(self) -> str:
        return self._seal_reason

    def seal(self, reason: str = "post-boot governance lock") -> None:
        """Freeze governance configuration — feature flag writes blocked."""
        self._sealed = True
        self._seal_reason = reason
        logger.info("governance sealed: %s", reason)

    def unseal(self, *, operator: str) -> None:
        """Explicitly unseal — requires operator identity for audit trail."""
        prev = self._seal_reason
        self._sealed = False
        self._seal_reason = ""
        logger.warning("governance unsealed by %s (was: %s)", operator, prev)

    # ── Pre-dispatch admission ───────────────────────────────────────

    async def pre_dispatch_admission(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        node_id: str,
        now: datetime.datetime,
    ) -> AdmissionResult:
        """Run admission control before the candidate query.

        Checks:
        1. Tenant pending/leased job count vs. AdmissionController limit.
        2. Node quarantine status (via FailureControlPlane).
        """
        from backend.core.scheduling_resilience import AdmissionController

        admitted, reason, details = await AdmissionController.check_admission(
            db,
            tenant_id,
        )
        if not admitted:
            return AdmissionResult(admitted=False, reason=reason, details=details)

        from backend.core.failure_control_plane import get_failure_control_plane

        fcp = get_failure_control_plane()
        if await fcp.is_node_quarantined(node_id, now=now):
            return AdmissionResult(
                admitted=False,
                reason="node_quarantined",
                details={"node_id": node_id},
            )

        return AdmissionResult(admitted=True)

    # ── Executor contract filter ─────────────────────────────────────

    def filter_by_executor_contract(
        self,
        executor: str,
        kind: str,
    ) -> ExecutorFilterResult:
        """Check if an executor supports a given job kind."""
        from backend.core.executor_registry import get_executor_registry

        compatible, reason = get_executor_registry().kind_compatible(executor, kind)
        return ExecutorFilterResult(compatible=compatible, reason=reason)

    # ── Strategy resolution ──────────────────────────────────────────

    def resolve_strategy(self, job_strategy: str | None) -> str:
        """Resolve the effective scheduling strategy for a job.

        Strategy selection is governed — only registered strategies are
        allowed. Unknown strategies fall back to the policy-store default.
        """
        from backend.core.scheduling_strategies import SchedulingStrategy

        if job_strategy:
            lower = job_strategy.lower()
            valid = {s.value for s in SchedulingStrategy}
            if lower in valid:
                return lower
        from backend.core.scheduling_policy_store import get_policy_store

        return get_policy_store().active.default_strategy

    # ── Post-dispatch audit ──────────────────────────────────────────

    async def post_dispatch_audit(
        self,
        db: AsyncSession,
        audit_logger: object,
        *,
        enabled: bool,
    ) -> None:
        """Flush the decision audit logger if the flag is enabled."""
        if enabled and hasattr(audit_logger, "flush"):
            await audit_logger.flush(db)

    # ── Guarded feature flag mutation ────────────────────────────────

    async def set_feature_guarded(
        self,
        db: AsyncSession,
        flag_key: str,
        enabled: bool,
    ) -> None:
        """Set a scheduling feature flag — blocked when governance is sealed."""
        if self._sealed:
            msg = f"governance is sealed ({self._seal_reason}); " f"cannot mutate flag '{flag_key}'. " f"Call unseal(operator=...) first."
            raise RuntimeError(msg)
        from backend.core.scheduling_governance import set_scheduling_feature

        await set_scheduling_feature(db, flag_key, enabled)

    # ── Preemption budget gate ───────────────────────────────────────

    def can_preempt(self, now: datetime.datetime) -> tuple[bool, str]:
        """Check preemption budget via resilience layer."""
        from backend.core.scheduling_resilience import PreemptionBudgetPolicy

        return PreemptionBudgetPolicy.can_preempt(now)

    def record_preemption(self, now: datetime.datetime) -> None:
        from backend.core.scheduling_resilience import PreemptionBudgetPolicy

        PreemptionBudgetPolicy.record_preemption(now)

    # ── Scheduling metrics proxy ─────────────────────────────────────

    def metrics_snapshot(self, window_seconds: int = 300) -> dict:
        """Return scheduling metrics snapshot."""
        from backend.core.scheduling_resilience import SchedulingMetrics

        return SchedulingMetrics.snapshot(window_seconds)

    def record_placement_metric(self, dispatch_ms: float) -> None:
        from backend.core.scheduling_resilience import SchedulingMetrics

        SchedulingMetrics.record_placement(dispatch_ms)

    def record_rejection_metric(self, reason: str) -> None:
        from backend.core.scheduling_resilience import SchedulingMetrics

        SchedulingMetrics.record_rejection(reason)

    def record_preemption_budget_hit(self) -> None:
        from backend.core.scheduling_resilience import SchedulingMetrics

        SchedulingMetrics.record_preemption_budget_hit()

    def record_backoff_skip_metric(self) -> None:
        from backend.core.scheduling_resilience import SchedulingMetrics

        SchedulingMetrics.record_backoff_skip()

    # ── Scheduling backoff proxy ─────────────────────────────────────

    def should_skip_backoff(self, job_id: str, now: datetime.datetime) -> bool:
        from backend.core.scheduling_resilience import SchedulingBackoff

        return SchedulingBackoff.should_skip(job_id, now)

    def record_backoff_failure(self, job_id: str, now: datetime.datetime) -> None:
        from backend.core.scheduling_resilience import SchedulingBackoff

        SchedulingBackoff.record_failure(job_id, now)

    def record_backoff_success(self, job_id: str) -> None:
        from backend.core.scheduling_resilience import SchedulingBackoff

        SchedulingBackoff.record_success(job_id)

    # ── Topology spread proxy ────────────────────────────────────────

    def configure_zone_context(self, zone_load: dict[str, int]) -> None:
        from backend.core.scheduling_resilience import TopologySpreadPolicy

        TopologySpreadPolicy.configure_zone_context(zone_load)

    # ── Decision audit factory ───────────────────────────────────────

    def create_decision_logger(
        self,
        tenant_id: str,
        node_id: str,
        now: datetime.datetime,
    ) -> object:
        """Create a SchedulingDecisionLogger for this dispatch cycle."""
        from backend.core.scheduling_governance import SchedulingDecisionLogger

        return SchedulingDecisionLogger(tenant_id=tenant_id, node_id=node_id, now=now)

    # ── Feature flag queries ─────────────────────────────────────────

    async def is_feature_enabled(self, db: AsyncSession, flag_key: str) -> bool:
        from backend.core.scheduling_governance import is_scheduling_feature_enabled

        return await is_scheduling_feature_enabled(db, flag_key)

    # ── Failure control plane proxy ──────────────────────────────────

    async def is_node_quarantined(self, node_id: str, now: datetime.datetime) -> bool:
        """Check node quarantine via FailureControlPlane."""
        from backend.core.failure_control_plane import get_failure_control_plane

        return await get_failure_control_plane().is_node_quarantined(node_id, now=now)

    async def is_connector_cooling(self, connector_id: str, now: datetime.datetime) -> bool:
        from backend.core.failure_control_plane import get_failure_control_plane

        return await get_failure_control_plane().is_connector_cooling(connector_id, now=now)

    async def get_kind_circuit_state(self, kind: str, now: datetime.datetime) -> str:
        from backend.core.failure_control_plane import get_failure_control_plane

        return await get_failure_control_plane().get_kind_circuit_state(kind, now=now)

    def is_in_burst(self, now: datetime.datetime) -> bool:
        from backend.core.failure_control_plane import get_failure_control_plane

        return get_failure_control_plane().is_in_burst(now=now)

    async def fcp_snapshot(self, now: datetime.datetime) -> dict:
        from backend.core.failure_control_plane import get_failure_control_plane

        return await get_failure_control_plane().snapshot(now=now)

    # ── Fair scheduler proxy ─────────────────────────────────────────

    def get_tenant_quota(self, tenant_id: str) -> object:
        from backend.core.queue_stratification import get_fair_scheduler

        return get_fair_scheduler().get_quota(tenant_id)

    def apply_fair_share(self, candidates: list) -> list:
        from backend.core.queue_stratification import get_fair_scheduler

        return get_fair_scheduler().apply_fair_share(candidates)

    def invalidate_fair_share_cache(self) -> None:
        from backend.core.queue_stratification import get_fair_scheduler

        get_fair_scheduler().invalidate_cache()

    # ── Placement solver proxy ───────────────────────────────────────

    def run_placement_solver(
        self,
        jobs: list,
        nodes: list,
        *,
        now: datetime.datetime,
        accepted_kinds: set[str],
        recent_failed_job_ids: set[str] | None = None,
    ) -> dict[str, str]:
        """Run the global placement solver and return {job_id: node_id} hints."""
        from backend.core.job_scheduler import get_placement_solver

        return get_placement_solver().solve(
            jobs,
            nodes,
            now=now,
            accepted_kinds=accepted_kinds,
            recent_failed_job_ids=recent_failed_job_ids,
        )

    # ── Dispatch lifecycle proxy ─────────────────────────────────────

    def get_dispatch_pipeline(self) -> object:
        from backend.core.dispatch_lifecycle import get_dispatch_pipeline

        return get_dispatch_pipeline()

    # ── Executor registry proxy ──────────────────────────────────────

    def validate_node_executor(
        self,
        executor: str,
        *,
        memory_mb: int = 0,
        cpu_cores: int = 0,
        gpu_vram_mb: int = 0,
    ) -> list[str]:
        from backend.core.executor_registry import get_executor_registry

        return get_executor_registry().validate_node_executor(
            executor,
            memory_mb=memory_mb,
            cpu_cores=cpu_cores,
            gpu_vram_mb=gpu_vram_mb,
        )

    def get_executor_contract(self, executor: str) -> object | None:
        from backend.core.executor_registry import get_executor_registry

        return get_executor_registry().get(executor)

    # ── Scheduler auto-tune proxy ────────────────────────────────────

    def tuner_snapshot(self) -> dict[str, object]:
        """Return full auto-tune diagnostic snapshot."""
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        return get_scheduler_tuner().snapshot()

    def tuner_enabled(self) -> bool:
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        return get_scheduler_tuner().enabled

    def set_tuner_enabled(self, value: bool) -> None:
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        get_scheduler_tuner().set_enabled(value)

    def reset_tuner(self) -> None:
        """Clear all learned state — revert to baseline weights."""
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        get_scheduler_tuner().reset()

    def get_tuner_adjustment(self, dimension: str) -> float:
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        return get_scheduler_tuner().get_adjustment(dimension)

    def get_tuner_node_bias(self, node_id: str) -> float:
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        return get_scheduler_tuner().get_node_bias(node_id)

    def get_tuner_kind_risk(self, kind: str) -> float:
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        return get_scheduler_tuner().get_kind_risk(kind)

    def tuner_recommend_strategy(self) -> str | None:
        from backend.core.scheduler_auto_tune import get_scheduler_tuner

        return get_scheduler_tuner().recommend_strategy()

    # ── Scheduling policy store proxy ────────────────────────────────

    def policy_snapshot(self) -> dict[str, object]:
        """Return full policy store diagnostic snapshot."""
        from backend.core.scheduling_policy_store import get_policy_store

        return get_policy_store().snapshot()

    def apply_policy(
        self,
        new_policy: object,
        *,
        operator: str = "system",
        reason: str = "",
    ) -> int:
        """Apply a new scheduling policy and return the new version number."""
        from backend.core.scheduling_policy_store import get_policy_store

        return get_policy_store().apply(new_policy, operator=operator, reason=reason).version  # type: ignore[arg-type]

    def rollback_policy(self, target_version: int, *, operator: str = "system") -> int:
        """Rollback to a previous policy version."""
        from backend.core.scheduling_policy_store import get_policy_store

        return get_policy_store().rollback(target_version, operator=operator).version

    def freeze_policy(self, *, reason: str = "") -> None:
        """Freeze the policy store — prevent further mutations."""
        from backend.core.scheduling_policy_store import get_policy_store

        get_policy_store().freeze(reason=reason)

    def unfreeze_policy(self, *, operator: str = "system") -> None:
        """Unfreeze the policy store — allow mutations again."""
        from backend.core.scheduling_policy_store import get_policy_store

        get_policy_store().unfreeze(operator=operator)

    def get_policy_version(self, version: int) -> object | None:
        """Retrieve a specific policy version record."""
        from backend.core.scheduling_policy_store import get_policy_store

        return get_policy_store().get_version_detail(version)

    @property
    def active_policy(self) -> object:
        """Return the currently active SchedulingPolicy."""
        from backend.core.scheduling_policy_store import get_policy_store

        return get_policy_store().active


# ── Module-level singleton ────────────────────────────────────────────

_facade: GovernanceFacade | None = None


def get_governance_facade() -> GovernanceFacade:
    """Return the process-wide GovernanceFacade singleton."""
    global _facade
    if _facade is None:
        _facade = GovernanceFacade()
    return _facade
