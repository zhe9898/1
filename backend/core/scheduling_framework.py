"""Pluggable Scheduling Framework — phase-based extensible pipeline.

Inspired by Kubernetes scheduling-framework (KEP-624), this module
formalises scheduling into distinct, named phases with typed plugin
interfaces.  Business tenants can register custom plugins without
modifying the core scheduler.

Phases (in execution order):
1. **QueueSort**   — custom ordering of the candidate queue.
2. **PreFilter**   — lightweight pre-admission (e.g., quota check).
3. **Filter**      — hard feasibility gates (resource fit, affinity…).
4. **PostFilter**  — after filtering — e.g., log dropped candidates.
5. **Score**       — soft scoring modifiers (fair-share, priority boost).
6. **Reserve**     — tentatively claim resources for accepted jobs.
7. **Permit**      — final admission control before lease (gang wait).
8. **PreBind**     — pre-binding hooks (e.g., create volumes).
9. **Bind**        — actual lease/assignment.
10. **PostBind**   — post-binding side effects (audit, metrics).

Each phase accepts a list of ``SchedulingPlugin`` instances. Plugins are
registered with ``SchedulingProfile`` and executed by the
``SchedulingPipeline``.

References:
- K8s scheduling-framework: https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
- Nomad scheduler extensibility: eval → plan → apply
- Slurm plugin architecture: sched/backfill, select/*, priority/*
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.scheduling_constraints import SchedulingContext
    from backend.models.job import Job

logger = logging.getLogger(__name__)


# =====================================================================
# Plugin result codes
# =====================================================================


class PluginStatus(Enum):
    """Result status from a scheduling plugin invocation."""

    SUCCESS = "success"
    REJECT = "reject"  # Hard rejection — job is dropped
    SKIP = "skip"  # Plugin has nothing to do for this job
    WAIT = "wait"  # Permit phase: hold the job (gang scheduling)
    ERROR = "error"  # Plugin errored — treatment depends on tolerance


@dataclass(frozen=True, slots=True)
class PluginResult:
    """Return value from every plugin hook."""

    status: PluginStatus
    reason: str = ""
    score_delta: int = 0  # Only meaningful for Score plugins


# =====================================================================
# Phase-specific plugin interfaces
# =====================================================================


class QueueSortPlugin(ABC):
    """Custom candidate queue ordering."""

    name: str = "queue_sort"

    @abstractmethod
    def less(self, a: Job, b: Job) -> bool:
        """Return True if *a* should be scheduled before *b*."""
        ...


class PreFilterPlugin(ABC):
    """Lightweight pre-admission check (runs once per scheduling cycle)."""

    name: str = "pre_filter"

    @abstractmethod
    def pre_filter(self, ctx: SchedulingContext) -> PluginResult:
        """Optionally reject the entire cycle or modify shared context."""
        ...


class FilterPlugin(ABC):
    """Hard feasibility gate — per-job evaluation."""

    name: str = "filter"

    @abstractmethod
    def filter(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Return REJECT to drop the job, SUCCESS to keep it."""
        ...


class PostFilterPlugin(ABC):
    """Called after Filter for observability or fallback logic."""

    name: str = "post_filter"

    @abstractmethod
    def post_filter(
        self,
        rejected: list[tuple[Job, str]],
        ctx: SchedulingContext,
    ) -> PluginResult:
        """Inspect rejected jobs (e.g., trigger backfill or preemption)."""
        ...


class ScorePlugin(ABC):
    """Soft scoring modifier — per-job evaluation."""

    name: str = "score"

    @abstractmethod
    def score(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Return score_delta in the result to adjust the job's score."""
        ...


class ReservePlugin(ABC):
    """Tentatively claim resources for a job about to be dispatched."""

    name: str = "reserve"

    @abstractmethod
    def reserve(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Return SUCCESS to proceed, REJECT to abort this dispatch."""
        ...

    def unreserve(self, job: Job, ctx: SchedulingContext) -> None:
        """Rollback a previous reserve() call (best-effort)."""


class PermitPlugin(ABC):
    """Final admission control — can WAIT (gang) or REJECT."""

    name: str = "permit"

    @abstractmethod
    def permit(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Return WAIT to hold the job (e.g., gang not ready)."""
        ...


class PreBindPlugin(ABC):
    """Pre-binding hook — called after permit, before lease."""

    name: str = "pre_bind"

    @abstractmethod
    def pre_bind(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Prepare resources before binding."""
        ...


class BindPlugin(ABC):
    """Custom binding logic (override default lease behaviour)."""

    name: str = "bind"

    @abstractmethod
    def bind(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Perform custom binding. Return SUCCESS if handled."""
        ...


class PostBindPlugin(ABC):
    """Post-binding side effects (audit, metrics, notifications)."""

    name: str = "post_bind"

    @abstractmethod
    def post_bind(self, job: Job, ctx: SchedulingContext) -> None:
        """Fire-and-forget after successful binding."""
        ...


# =====================================================================
# Scheduling Profile — plugin registry per scheduling profile
# =====================================================================


@dataclass
class SchedulingProfile:
    """Named collection of plugins forming a scheduling configuration.

    Multiple profiles can coexist (e.g., "default", "batch", "realtime")
    and be selected per-tenant or per-job-kind.
    """

    name: str = "default"
    queue_sort: list[QueueSortPlugin] = field(default_factory=list)
    pre_filters: list[PreFilterPlugin] = field(default_factory=list)
    filters: list[FilterPlugin] = field(default_factory=list)
    post_filters: list[PostFilterPlugin] = field(default_factory=list)
    scorers: list[ScorePlugin] = field(default_factory=list)
    reservers: list[ReservePlugin] = field(default_factory=list)
    permits: list[PermitPlugin] = field(default_factory=list)
    pre_binders: list[PreBindPlugin] = field(default_factory=list)
    binders: list[BindPlugin] = field(default_factory=list)
    post_binders: list[PostBindPlugin] = field(default_factory=list)


# =====================================================================
# Scheduling Pipeline — executes plugins through phases
# =====================================================================


class SchedulingPipeline:
    """Execute a scheduling profile against a candidate list.

    The pipeline is stateless — create one per dispatch cycle with the
    appropriate profile.

    Usage::

        profile = SchedulingProfile(name="batch")
        profile.filters.append(MyCustomFilter())
        profile.scorers.append(MyCustomScorer())

        pipeline = SchedulingPipeline(profile)
        result = pipeline.run(candidates, ctx)
    """

    def __init__(self, profile: SchedulingProfile) -> None:
        self._profile = profile

    @property
    def profile_name(self) -> str:
        return self._profile.name

    def run_queue_sort(self, candidates: list[Job]) -> list[Job]:
        """Phase 1: QueueSort — reorder candidates."""
        if not self._profile.queue_sort:
            return candidates
        import functools

        sorter = self._profile.queue_sort[0]  # only first wins

        def _cmp(a: Job, b: Job) -> int:
            if sorter.less(a, b):
                return -1
            if sorter.less(b, a):
                return 1
            return 0

        return sorted(candidates, key=functools.cmp_to_key(_cmp))

    def run_pre_filter(self, ctx: SchedulingContext) -> PluginResult:
        """Phase 2: PreFilter — cycle-level admission."""
        for plugin in self._profile.pre_filters:
            result = plugin.pre_filter(ctx)
            if result.status == PluginStatus.REJECT:
                logger.debug("PreFilter %s rejected cycle: %s", plugin.name, result.reason)
                return result
        return PluginResult(status=PluginStatus.SUCCESS)

    def run_filter(
        self,
        candidates: list[Job],
        ctx: SchedulingContext,
    ) -> tuple[list[Job], list[tuple[Job, str]]]:
        """Phase 3: Filter — per-job hard gates.

        Returns (accepted, rejected_with_reasons).
        """
        accepted: list[Job] = []
        rejected: list[tuple[Job, str]] = []
        for job in candidates:
            passed = True
            for plugin in self._profile.filters:
                result = plugin.filter(job, ctx)
                if result.status == PluginStatus.REJECT:
                    rejected.append((job, f"{plugin.name}:{result.reason}"))
                    passed = False
                    break
            if passed:
                accepted.append(job)
        return accepted, rejected

    def run_post_filter(
        self,
        rejected: list[tuple[Job, str]],
        ctx: SchedulingContext,
    ) -> None:
        """Phase 4: PostFilter — observability / fallback."""
        for plugin in self._profile.post_filters:
            plugin.post_filter(rejected, ctx)

    def run_score(
        self,
        candidates: list[Job],
        ctx: SchedulingContext,
    ) -> dict[str, int]:
        """Phase 5: Score — collect score deltas per job.

        Returns {job_id: total_score_delta}.
        """
        deltas: dict[str, int] = {}
        for job in candidates:
            total = 0
            for plugin in self._profile.scorers:
                result = plugin.score(job, ctx)
                total += result.score_delta
            deltas[job.job_id] = total
        return deltas

    def run_reserve(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Phase 6: Reserve — claim resources."""
        reserved_plugins: list[ReservePlugin] = []
        for plugin in self._profile.reservers:
            result = plugin.reserve(job, ctx)
            if result.status == PluginStatus.REJECT:
                # Rollback already-reserved plugins
                for rp in reversed(reserved_plugins):
                    rp.unreserve(job, ctx)
                return result
            reserved_plugins.append(plugin)
        return PluginResult(status=PluginStatus.SUCCESS)

    def run_permit(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Phase 7: Permit — final admission (may WAIT for gang)."""
        for plugin in self._profile.permits:
            result = plugin.permit(job, ctx)
            if result.status in (PluginStatus.REJECT, PluginStatus.WAIT):
                return result
        return PluginResult(status=PluginStatus.SUCCESS)

    def run_pre_bind(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Phase 8: PreBind — prepare before lease."""
        for plugin in self._profile.pre_binders:
            result = plugin.pre_bind(job, ctx)
            if result.status == PluginStatus.REJECT:
                return result
        return PluginResult(status=PluginStatus.SUCCESS)

    def run_bind(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        """Phase 9: Bind — perform lease assignment."""
        for plugin in self._profile.binders:
            result = plugin.bind(job, ctx)
            if result.status == PluginStatus.SUCCESS:
                return result  # first successful binder wins
        # No custom binder — caller uses default lease logic
        return PluginResult(status=PluginStatus.SKIP)

    def run_post_bind(self, job: Job, ctx: SchedulingContext) -> None:
        """Phase 10: PostBind — fire-and-forget side effects."""
        for plugin in self._profile.post_binders:
            try:
                plugin.post_bind(job, ctx)
            except Exception:
                logger.warning("PostBind plugin %s failed", plugin.name, exc_info=True)

    def run_full(
        self,
        candidates: list[Job],
        ctx: SchedulingContext,
    ) -> list[Job]:
        """Execute the full filter+score pipeline (Phases 1-5).

        This is the drop-in replacement for ``SchedulingEngine.run()``.
        Reserve/Permit/Bind phases are called per-job during the actual
        lease loop in dispatch.py.
        """
        # Phase 1: QueueSort
        sorted_candidates = self.run_queue_sort(candidates)

        # Phase 2: PreFilter
        pre_result = self.run_pre_filter(ctx)
        if pre_result.status == PluginStatus.REJECT:
            return []

        # Phase 3: Filter
        accepted, rejected = self.run_filter(sorted_candidates, ctx)

        # Phase 4: PostFilter
        self.run_post_filter(rejected, ctx)

        # Phase 5: Score (adjust priorities via score_delta)
        if self._profile.scorers:
            deltas = self.run_score(accepted, ctx)
            for job in accepted:
                delta = deltas.get(job.job_id, 0)
                if delta:
                    job.priority = max(0, min(160, int(job.priority or 0) + delta))

        return accepted


# =====================================================================
# Profile Registry — named profiles for different workload classes
# =====================================================================

_profiles: dict[str, SchedulingProfile] = {}


def register_profile(profile: SchedulingProfile) -> None:
    """Register a scheduling profile by name."""
    _profiles[profile.name] = profile


def get_profile(name: str = "default") -> SchedulingProfile:
    """Get a scheduling profile by name, or the default."""
    return _profiles.get(name, SchedulingProfile(name=name))


def list_profiles() -> list[str]:
    """Return names of all registered profiles."""
    return sorted(_profiles.keys())


# =====================================================================
# Built-in adapter: bridge existing SchedulingConstraint → FilterPlugin
# =====================================================================


class ConstraintFilterAdapter(FilterPlugin):
    """Adapter that wraps a legacy SchedulingConstraint as a FilterPlugin.

    This allows the existing constraint pipeline to work inside the new
    framework without rewriting all gates.
    """

    def __init__(self, constraint: object) -> None:
        from backend.core.scheduling_constraints import SchedulingConstraint as SC

        if not isinstance(constraint, SC):
            raise TypeError(f"Expected SchedulingConstraint, got {type(constraint)}")
        self._constraint = constraint
        self.name = f"constraint:{constraint.name}"

    def filter(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        ok, reason = self._constraint.evaluate(job, ctx)
        if ok:
            return PluginResult(status=PluginStatus.SUCCESS)
        if self._constraint.hard:
            return PluginResult(status=PluginStatus.REJECT, reason=reason)
        return PluginResult(status=PluginStatus.SUCCESS, score_delta=-10)


class ConstraintScoreAdapter(ScorePlugin):
    """Adapter that wraps a soft SchedulingConstraint as a ScorePlugin."""

    def __init__(self, constraint: object) -> None:
        from backend.core.scheduling_constraints import SchedulingConstraint as SC

        if not isinstance(constraint, SC):
            raise TypeError(f"Expected SchedulingConstraint, got {type(constraint)}")
        self._constraint = constraint
        self.name = f"score:{constraint.name}"

    def score(self, job: Job, ctx: SchedulingContext) -> PluginResult:
        self._constraint.evaluate(job, ctx)
        return PluginResult(status=PluginStatus.SUCCESS, score_delta=0)


def build_profile_from_engine() -> SchedulingProfile:
    """Build a SchedulingProfile from the existing SchedulingEngine.

    This is the migration bridge — existing gates become Filter/Score
    plugins inside a profile, enabling gradual migration to the
    framework model.
    """
    from backend.core.scheduling_constraints import get_scheduling_engine

    engine = get_scheduling_engine()
    profile = SchedulingProfile(name="default")

    for constraint in engine._constraints:
        if constraint.hard:
            profile.filters.append(ConstraintFilterAdapter(constraint))
        else:
            profile.scorers.append(ConstraintScoreAdapter(constraint))

    return profile
