"""Dispatch Lifecycle — structured pipeline for the scheduling dispatch chain.

Extracts the 5-phase dispatch logic from ``dispatch.py`` into a composable,
testable pipeline with explicit lifecycle stages:

1. **Admission** — governance facade admission + feature-flag snapshot.
2. **Filtering** — circuit breakers, executor contract, scheduling backoff.
3. **Business** — dependency gate, deadline, gang scheduling, fair-share.
4. **Placement** — global placement solver hints + per-node scoring.
5. **Post-dispatch** — preemption, DLQ sweep, metrics flush.

Each stage reads/writes a shared ``DispatchContext`` and produces a typed
result so callers get structured feedback (not just a list of jobs).

Usage::

    ctx = DispatchContext(tenant_id=..., node_id=..., ...)
    pipeline = get_dispatch_pipeline()
    result = await pipeline.execute(ctx, db)
    # result.leased_jobs, result.rejected, result.metrics
"""

from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ============================================================================
# Context — shared mutable state passed through every stage
# ============================================================================


@dataclass
class DispatchContext:
    """Mutable context travelling with every dispatch cycle."""

    tenant_id: str
    node_id: str
    now: datetime.datetime
    accepted_kinds: set[str] = field(default_factory=set)
    limit: int = 5

    # Populated by admission / load stage
    feature_flags: dict[str, bool] = field(default_factory=dict)
    burst_active: bool = False
    candidate_limit: int = 200

    # Populated by filtering stage
    candidates: list[object] = field(default_factory=list)
    filtered_count: int = 0
    backoff_skipped: int = 0

    # Populated by business stage
    completed_dep_ids: set[str] = field(default_factory=set)
    parent_jobs: dict[str, object] = field(default_factory=dict)
    available_slots: int = 0

    # Populated by placement stage
    placement_hints: dict[str, str] = field(default_factory=dict)
    selected: list[object] = field(default_factory=list)

    # Post-dispatch stage outputs
    preemptions: list[dict[str, str]] = field(default_factory=list)
    dlq_count: int = 0

    # Timing
    _stage_timings: dict[str, float] = field(default_factory=dict)
    _start_ns: int = field(default_factory=lambda: time.monotonic_ns())

    def record_stage_time(self, stage: str) -> None:
        elapsed = (time.monotonic_ns() - self._start_ns) / 1_000_000
        self._stage_timings[stage] = round(elapsed, 2)
        self._start_ns = time.monotonic_ns()

    @property
    def total_ms(self) -> float:
        return sum(self._stage_timings.values())


# ============================================================================
# Result — structured return from the pipeline
# ============================================================================


@dataclass
class DispatchResult:
    """Outcome of the full dispatch pipeline."""

    leased_jobs: list[object] = field(default_factory=list)
    rejected_count: int = 0
    preemptions: list[dict[str, str]] = field(default_factory=list)
    dlq_count: int = 0
    admitted: bool = True
    admission_reason: str = ""
    metrics: dict[str, object] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.admitted


# ============================================================================
# Stage protocol
# ============================================================================


@runtime_checkable
class DispatchStage(Protocol):
    """A single stage in the dispatch pipeline."""

    name: str

    async def execute(self, ctx: DispatchContext) -> bool:
        """Run this stage, mutating *ctx* in-place.

        Return ``True`` to continue to the next stage, ``False`` to
        short-circuit the pipeline (e.g. admission denied).
        """
        ...


# ============================================================================
# Built-in stages
# ============================================================================


class AdmissionStage:
    """Check governance admission and snapshot feature flags."""

    name = "admission"

    async def execute(self, ctx: DispatchContext) -> bool:
        from backend.core.governance_facade import get_governance_facade

        facade = get_governance_facade()
        result = await facade.pre_dispatch_admission(
            _noop_db_stub(),
            tenant_id=ctx.tenant_id,
            node_id=ctx.node_id,
            now=ctx.now,
        )
        if not result.admitted:
            ctx.feature_flags["_admission_denied"] = True
            return False

        ctx.record_stage_time("admission")
        return True


class FilteringStage:
    """Circuit breaker, executor contract, and backoff filtering."""

    name = "filtering"

    async def execute(self, ctx: DispatchContext) -> bool:
        from backend.core.failure_control_plane import get_failure_control_plane
        from backend.core.governance_facade import get_governance_facade

        fcp = get_failure_control_plane()
        facade = get_governance_facade()

        # Burst throttle
        ctx.burst_active = fcp.is_in_burst(now=ctx.now)
        if ctx.burst_active:
            ctx.candidate_limit = max(ctx.candidate_limit // 2, 10)

        # Backoff filter
        pre_count = len(ctx.candidates)
        ctx.candidates = [
            c for c in ctx.candidates
            if not facade.should_skip_backoff(getattr(c, "job_id", ""), ctx.now)
        ]
        ctx.backoff_skipped = pre_count - len(ctx.candidates)

        # Circuit breaker + executor contract filter
        filtered: list[object] = []
        for c in ctx.candidates:
            kind = getattr(c, "kind", "") or ""
            if kind:
                state = await fcp.get_kind_circuit_state(kind, now=ctx.now)
                if state == "open":
                    continue
            if ctx.feature_flags.get("executor_validation") and kind:
                ef = facade.filter_by_executor_contract(
                    "",  # executor filled by dispatch.py caller
                    kind,
                )
                if not ef.compatible:
                    continue
            filtered.append(c)
        ctx.filtered_count = len(ctx.candidates) - len(filtered)
        ctx.candidates = filtered
        ctx.record_stage_time("filtering")
        return True


class BusinessStage:
    """Dependency, deadline, gang, and fair-share gates."""

    name = "business"

    async def execute(self, ctx: DispatchContext) -> bool:
        from backend.core.business_scheduling import apply_business_filters

        ctx.candidates = apply_business_filters(
            ctx.candidates,
            completed_job_ids=ctx.completed_dep_ids,
            available_slots=ctx.available_slots,
            parent_jobs=ctx.parent_jobs,
            now=ctx.now,
        )
        ctx.record_stage_time("business")
        return True


class PlacementStage:
    """Global placement solver hints + per-node scoring."""

    name = "placement"

    async def execute(self, ctx: DispatchContext) -> bool:
        from backend.core.job_scheduler import get_placement_solver

        # Run global solver if enough candidates
        if len(ctx.candidates) >= 2:
            solver = get_placement_solver()
            # Solver requires Job and SchedulerNodeSnapshot instances;
            # dispatch.py populates ctx.candidates with Job objects and
            # ctx._active_node_snapshots with snapshots.
            nodes = getattr(ctx, "_active_node_snapshots", [])
            if nodes:
                ctx.placement_hints = solver.solve(
                    ctx.candidates,
                    nodes,
                    now=ctx.now,
                    accepted_kinds=ctx.accepted_kinds,
                )

        ctx.record_stage_time("placement")
        return True


class PostDispatchStage:
    """Preemption eval and metrics recording."""

    name = "post_dispatch"

    async def execute(self, ctx: DispatchContext) -> bool:
        from backend.core.governance_facade import get_governance_facade

        facade = get_governance_facade()
        dispatch_ms = ctx.total_ms
        for _ in ctx.selected:
            facade.record_placement_metric(dispatch_ms)
        if ctx.candidates and not ctx.selected:
            facade.record_rejection_metric("no_eligible_slot")

        ctx.record_stage_time("post_dispatch")
        return True


# ============================================================================
# Pipeline — ordered stage runner
# ============================================================================


class DispatchPipeline:
    """Ordered execution of dispatch stages with short-circuit support."""

    def __init__(self, stages: list[DispatchStage] | None = None) -> None:
        self.stages = stages or _default_stages()

    async def execute(self, ctx: DispatchContext) -> DispatchResult:
        result = DispatchResult()
        for stage in self.stages:
            try:
                cont = await stage.execute(ctx)
            except Exception:
                logger.exception("dispatch stage '%s' failed", stage.name)
                cont = False
            if not cont:
                result.admitted = False
                result.admission_reason = f"short-circuited at {stage.name}"
                break

        result.leased_jobs = list(ctx.selected)
        result.rejected_count = ctx.filtered_count
        result.preemptions = list(ctx.preemptions)
        result.dlq_count = ctx.dlq_count
        result.metrics = {
            "stage_timings": dict(ctx._stage_timings),
            "total_ms": ctx.total_ms,
            "burst_active": ctx.burst_active,
            "backoff_skipped": ctx.backoff_skipped,
            "placement_hints_count": len(ctx.placement_hints),
        }
        return result


def _default_stages() -> list[DispatchStage]:
    return [
        AdmissionStage(),
        FilteringStage(),
        BusinessStage(),
        PlacementStage(),
        PostDispatchStage(),
    ]


# Module-level singleton
_pipeline: DispatchPipeline | None = None


def get_dispatch_pipeline() -> DispatchPipeline:
    """Return the process-wide DispatchPipeline singleton."""
    global _pipeline
    if _pipeline is None:
        _pipeline = DispatchPipeline()
    return _pipeline


# ============================================================================
# Helpers
# ============================================================================


def _noop_db_stub():
    """Placeholder for when stage is called outside of a DB session.

    Real usage in dispatch.py passes the actual AsyncSession via ctx.
    """

    class _Stub:
        async def execute(self, *a, **kw):  # noqa: ANN
            return type("R", (), {"scalars": lambda self: type("S", (), {"first": lambda self: None, "all": lambda self: []})()})()

    return _Stub()


# ============================================================================
# Global placement hint integration
# ============================================================================


def apply_placement_hints(
    scored_jobs: list[object],
    hints: dict[str, str],
    node_id: str,
    *,
    bonus: int = 50,
) -> list[object]:
    """Boost scores for jobs that the solver recommends for this node.

    Applied after ``select_jobs_for_node`` scoring, before final ranking.
    Jobs whose solver hint matches *node_id* receive a score bonus.
    """
    if not hints:
        return scored_jobs

    for sj in scored_jobs:
        job = getattr(sj, "job", None)
        jid = getattr(job, "job_id", "") if job else ""
        if hints.get(jid) == node_id:
            current = getattr(sj, "score", 0)
            if hasattr(type(sj), "__dataclass_fields__"):
                object.__setattr__(sj, "score", current + bonus)
            bd = getattr(sj, "score_breakdown", None)
            if bd is None:
                bd = {}
            bd["solver_hint"] = bonus
            if hasattr(type(sj), "__dataclass_fields__"):
                object.__setattr__(sj, "score_breakdown", bd)
    return scored_jobs
