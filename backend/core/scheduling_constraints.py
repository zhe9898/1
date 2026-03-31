"""Scheduling constraint pipeline: class-based, registerable, observable.

Extracted from business_scheduling.py for maintainability.
Contains the SchedulingConstraint base, built-in gates, SchedulingContext,
SchedulingEngine, and the module-level singleton.
"""
from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.models.job import Job


# =====================================================================
# Constraint engine: class-based, registerable, observable
# =====================================================================


class SchedulingConstraint(ABC):
    """Base class for a hard scheduling gate or soft priority modifier.

    Subclasses implement ``evaluate`` which returns ``(pass, reason)``.
    The engine calls constraints in ``order`` (ascending) and tracks
    per-gate drop counts for observability.
    """

    name: str = "base"
    order: int = 100  # lower = earlier in pipeline
    hard: bool = True  # hard gate drops, soft gate only modifies

    @abstractmethod
    def evaluate(
        self,
        job: Job,
        ctx: SchedulingContext,
    ) -> tuple[bool, str]:
        """Return (passed, reason). If hard and not passed -> job dropped."""
        ...


class SchedulingContext:
    """Shared context passed through the constraint pipeline."""

    __slots__ = (
        "now",
        "completed_job_ids",
        "available_slots",
        "parent_jobs",
        "surviving_candidates",
        "stats",
        "data",
    )

    def __init__(
        self,
        *,
        now: datetime.datetime,
        completed_job_ids: set[str],
        available_slots: int,
        parent_jobs: dict[str, Job],
    ) -> None:
        self.now = now
        self.completed_job_ids = completed_job_ids
        self.available_slots = available_slots
        self.parent_jobs = parent_jobs
        self.surviving_candidates: list[Job] = []
        self.stats: dict[str, int] = {}  # constraint_name -> drop_count
        self.data: dict[str, object] = {}  # extension data for constraints


# -- Built-in constraints --


class DeadlineExpiryGate(SchedulingConstraint):
    """Drop jobs whose deadline has already passed."""

    name = "deadline_expiry"
    order = 10

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        deadline = getattr(job, "deadline_at", None)
        if deadline is not None and deadline <= ctx.now:
            return False, "deadline_expired"
        return True, ""


class DependencyGate(SchedulingConstraint):
    """Drop jobs with unresolved dependency chains."""

    name = "dependency"
    order = 20

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        from backend.core.business_scheduling import check_job_dependencies_satisfied

        depends_on = getattr(job, "depends_on", None) or []
        if not depends_on:
            return True, ""
        satisfied, blocking = check_job_dependencies_satisfied(
            job, ctx.completed_job_ids,
        )
        if not satisfied:
            return False, f"blocked_by:{','.join(blocking[:3])}"
        return True, ""


class GangSchedulingGate(SchedulingConstraint):
    """Drop jobs whose gang members aren't all ready."""

    name = "gang_scheduling"
    order = 30

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        from backend.core.business_scheduling import calculate_gang_scheduling_readiness

        gang_id = getattr(job, "gang_id", None)
        if not gang_id:
            return True, ""
        ready, reason = calculate_gang_scheduling_readiness(
            job, ctx.surviving_candidates, ctx.available_slots,
        )
        if not ready:
            return False, reason
        return True, ""


class PriorityBoostModifier(SchedulingConstraint):
    """Soft constraint -- boosts priority based on deadline/SLA/parent inheritance."""

    name = "priority_boost"
    order = 40
    hard = False

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        from backend.core.business_scheduling import calculate_boosted_priority

        if job.deadline_at or job.sla_seconds or job.parent_job_id:
            boosted = calculate_boosted_priority(
                job, now=ctx.now, parent_jobs=ctx.parent_jobs,
            )
            if boosted > job.priority:
                job.priority = boosted
        return True, ""


class ConnectorCoolingGate(SchedulingConstraint):
    """Drop jobs whose connector is in cooling period."""

    name = "connector_cooling"
    order = 15

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        connector_id = getattr(job, "connector_id", None)
        if not connector_id:
            return True, ""
        # Connector cooling state is checked via the failure control plane
        # which is process-local. Import lazily to avoid circular deps.
        from backend.core.failure_control_plane import get_failure_control_plane
        fcp = get_failure_control_plane()
        until = fcp._connector_cool_until.get(connector_id)
        if until is not None and ctx.now < until:
            return False, f"connector_cooling_until:{until.isoformat()}"
        return True, ""


class TenantFairShareGate(SchedulingConstraint):
    """Enforce per-tenant fair-share quota from config.

    Uses GlobalFairScheduler to load per-tenant quotas from system.yaml.
    Tracks per-tenant dispatch counts in ``ctx.data["_tenant_dispatched"]``
    and rejects jobs once a tenant's round quota is exhausted.
    """

    name = "tenant_fair_share"
    order = 8  # Early -- before dependency/gang checks to save work
    hard = True

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        from backend.core.queue_stratification import get_fair_scheduler

        fs = get_fair_scheduler()
        tenant_id = getattr(job, "tenant_id", "default")
        quota = fs.get_quota(tenant_id)

        # Lazy-init per-run tracking
        key = "_tenant_dispatched"
        if key not in ctx.data:
            ctx.data[key] = {}
        dispatched: dict[str, int] = ctx.data[key]  # type: ignore[assignment]

        count = dispatched.get(tenant_id, 0)
        if count >= quota.max_jobs_per_round:
            return False, f"tenant_quota_exhausted:{tenant_id}:{count}/{quota.max_jobs_per_round}"
        dispatched[tenant_id] = count + 1
        return True, ""


# -- Default pipeline --

_DEFAULT_CONSTRAINTS: list[SchedulingConstraint] = sorted(
    [
        TenantFairShareGate(),
        DeadlineExpiryGate(),
        ConnectorCoolingGate(),
        DependencyGate(),
        GangSchedulingGate(),
        PriorityBoostModifier(),
    ],
    key=lambda c: c.order,
)


class SchedulingEngine:
    """Evaluates a list of constraints against candidate jobs.

    Usage::

        engine = SchedulingEngine()
        engine.register(MyCustomGate())
        filtered = engine.run(candidates, ctx)
    """

    def __init__(self) -> None:
        self._constraints: list[SchedulingConstraint] = list(_DEFAULT_CONSTRAINTS)

    def register(self, constraint: SchedulingConstraint) -> None:
        """Add a constraint and re-sort by order."""
        self._constraints.append(constraint)
        self._constraints.sort(key=lambda c: c.order)

    def run(
        self,
        candidates: list[Job],
        ctx: SchedulingContext,
    ) -> list[Job]:
        """Evaluate all constraints and return surviving candidates."""
        # Two-pass: hard gates first (except gang which needs survivors),
        # then gang + soft modifiers.

        # Pass 1: non-gang hard gates
        after_hard: list[Job] = []
        for job in candidates:
            passed = True
            for constraint in self._constraints:
                if not constraint.hard:
                    continue
                if constraint.name == "gang_scheduling":
                    continue  # deferred to pass 2
                ok, _reason = constraint.evaluate(job, ctx)
                if not ok:
                    ctx.stats[constraint.name] = ctx.stats.get(constraint.name, 0) + 1
                    passed = False
                    break
            if passed:
                after_hard.append(job)

        # Pass 2: gang gate (needs surviving list)
        ctx.surviving_candidates = after_hard
        result: list[Job] = []
        gang_gate = next(
            (c for c in self._constraints if c.name == "gang_scheduling"), None,
        )
        for job in after_hard:
            if gang_gate:
                ok, _reason = gang_gate.evaluate(job, ctx)
                if not ok:
                    ctx.stats[gang_gate.name] = ctx.stats.get(gang_gate.name, 0) + 1
                    continue
            # Soft modifiers
            for constraint in self._constraints:
                if constraint.hard:
                    continue
                constraint.evaluate(job, ctx)
            result.append(job)

        return result


# Module-level singleton (process lifecycle)
_engine = SchedulingEngine()


def get_scheduling_engine() -> SchedulingEngine:
    """Return the module-level scheduling engine singleton."""
    return _engine
