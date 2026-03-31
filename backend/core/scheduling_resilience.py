"""Scheduling Resilience — industry-standard scheduling gaps filled.

Benchmarked against Kubernetes kube-scheduler, HashiCorp Nomad, and
Apache Mesos.  Implements five capabilities they provide that ZEN70
previously lacked:

1. **TopologySpreadPolicy** — zone-aware spreading with maxSkew
   (K8s TopologySpreadConstraints equivalent).
2. **PreemptionBudgetPolicy** — disruption budget limiting concurrent
   preemptions (K8s PodDisruptionBudget equivalent).
3. **SchedulingBackoff** — exponential retry backoff for unschedulable
   jobs (K8s unschedulable-backoff equivalent).
4. **AdmissionController** — queue depth backpressure rejecting
   submissions when tenant queue is saturated (K8s ResourceQuota /
   Nomad job-quota equivalent).
5. **SchedulingMetrics** — lightweight in-memory throughput, latency,
   and rejection tracking (K8s scheduler_framework_extension_point
   equivalent).
"""

from __future__ import annotations

import datetime
import logging
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from backend.core.job_scheduler import SchedulerNodeSnapshot, ScoredJob
    from backend.models.job import Job

logger = logging.getLogger(__name__)


# =====================================================================
# 1. Topology Spread Policy  (K8s TopologySpreadConstraints)
# =====================================================================


class TopologySpreadPolicy:
    """Penalise nodes in over-represented zones to achieve cross-zone spread.

    Before the scoring loop, the caller should invoke
    ``configure_zone_context()`` with current zone-level lease counts.
    ``adjust_score()`` then applies a penalty proportional to how much
    the target zone exceeds the average by more than ``max_skew``.

    Kubernetes equivalent: ``topologySpreadConstraints.maxSkew``.
    """

    name = "topology_spread"
    order = 15  # after resource_reservation (10), before thermal_cap (20)

    _zone_load: dict[str, int] = {}
    _avg_zone_load: float = 0.0
    _zone_count: int = 0

    def __init__(
        self,
        *,
        max_skew: int | None = None,
        penalty_per_skew: int | None = None,
        max_penalty: int | None = None,
    ) -> None:
        from backend.core.scheduling_policy_store import get_policy_store
        ts = get_policy_store().active.topology_spread
        self.max_skew = max_skew if max_skew is not None else ts.max_skew
        self.penalty_per_skew = penalty_per_skew if penalty_per_skew is not None else ts.penalty_per_skew
        self.max_penalty = max_penalty if max_penalty is not None else ts.max_penalty

    @classmethod
    def configure_zone_context(cls, zone_load: dict[str, int]) -> None:
        """Set current zone-level lease distribution (call before scoring)."""
        cls._zone_load = dict(zone_load)
        total = sum(zone_load.values())
        cls._zone_count = max(len(zone_load), 1)
        cls._avg_zone_load = total / cls._zone_count

    def adjust_score(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        current_score: int,
        breakdown: dict[str, int],
    ) -> tuple[int, dict[str, int]]:
        zone = node.zone or ""
        if not zone or not self._zone_load or self._zone_count <= 1:
            return current_score, breakdown

        my_load = self._zone_load.get(zone, 0)
        skew = my_load - self._avg_zone_load
        if skew > self.max_skew:
            penalty = min(
                int((skew - self.max_skew) * self.penalty_per_skew),
                self.max_penalty,
            )
            breakdown["topology_spread_penalty"] = -penalty
            return current_score - penalty, breakdown
        return current_score, breakdown

    def rerank(
        self,
        scored: list[ScoredJob],
        node: SchedulerNodeSnapshot,
    ) -> list[ScoredJob]:
        return scored

    def accept(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        score: int,
    ) -> tuple[bool, str]:
        return True, ""


# =====================================================================
# 2. Preemption Budget Policy  (K8s PodDisruptionBudget)
# =====================================================================


class PreemptionBudgetPolicy:
    """Rate-limit preemptions to prevent cascade disruption.

    Tracks recent preemptions in a sliding window and vetoes further
    preemptions once the budget is exhausted.  This is the system-level
    equivalent of Kubernetes PodDisruptionBudget.

    Usage::

        if PreemptionBudgetPolicy.can_preempt(now):
            # perform preemption
            PreemptionBudgetPolicy.record_preemption(now)
    """

    _recent_preemptions: deque[datetime.datetime] = deque(maxlen=500)  # overridden by _init_buffer
    max_preemptions_per_window: int | None = None
    window_seconds: int | None = None

    @classmethod
    def _resolve_limits(cls) -> tuple[int, int]:
        from backend.core.scheduling_policy_store import get_policy_store
        pp = get_policy_store().active.preemption
        return (
            cls.max_preemptions_per_window if cls.max_preemptions_per_window is not None else pp.max_per_window,
            cls.window_seconds if cls.window_seconds is not None else pp.window_seconds,
        )

    @classmethod
    def configure(cls, *, max_per_window: int = 5, window_s: int = 300) -> None:
        cls.max_preemptions_per_window = max_per_window
        cls.window_seconds = window_s

    @classmethod
    def can_preempt(cls, now: datetime.datetime) -> tuple[bool, str]:
        """Check whether preemption budget has capacity."""
        max_pw, window_s = cls._resolve_limits()
        cutoff = now - datetime.timedelta(seconds=window_s)
        recent = sum(1 for t in cls._recent_preemptions if t > cutoff)
        if recent >= max_pw:
            return False, (
                f"preemption_budget_exhausted: "
                f"{recent}/{max_pw} in {window_s}s"
            )
        return True, ""

    @classmethod
    def record_preemption(cls, now: datetime.datetime) -> None:
        cls._recent_preemptions.append(now)

    @classmethod
    def recent_count(cls, now: datetime.datetime) -> int:
        _, window_s = cls._resolve_limits()
        cutoff = now - datetime.timedelta(seconds=window_s)
        return sum(1 for t in cls._recent_preemptions if t > cutoff)

    @classmethod
    def reset(cls) -> None:
        cls._recent_preemptions.clear()


# =====================================================================
# 3. Scheduling Backoff  (K8s unschedulable-backoff)
# =====================================================================


@dataclass
class _BackoffEntry:
    attempts: int = 0
    next_try: datetime.datetime = field(default_factory=lambda: datetime.datetime.min)


class SchedulingBackoff:
    """Exponential backoff for jobs that repeatedly fail to schedule.

    When ``select_jobs_for_node`` returns nothing for a candidate, we
    call ``record_failure``.  Subsequent dispatch cycles skip that job
    until the backoff window elapses.  A successful lease clears the
    backoff state.

    Kubernetes equivalent: ``unschedulable-queue`` with exponential
    back-off (1s → 2s → 4s … → 5min cap).
    """

    BASE_DELAY_S: float | None = None
    MAX_DELAY_S: float | None = None
    MAX_ATTEMPTS: int | None = None
    _CLEANUP_INTERVAL: int = 500  # resolved from policy store

    _entries: dict[str, _BackoffEntry] = {}
    _call_counter: int = 0

    @classmethod
    def _resolve(cls) -> tuple[float, float, int]:
        from backend.core.scheduling_policy_store import get_policy_store
        bp = get_policy_store().active.backoff
        cls._CLEANUP_INTERVAL = bp.cleanup_interval
        return (
            cls.BASE_DELAY_S if cls.BASE_DELAY_S is not None else bp.base_delay_seconds,
            cls.MAX_DELAY_S if cls.MAX_DELAY_S is not None else bp.max_delay_seconds,
            cls.MAX_ATTEMPTS if cls.MAX_ATTEMPTS is not None else bp.max_attempts,
        )

    @classmethod
    def should_skip(cls, job_id: str, now: datetime.datetime) -> bool:
        """Return True if the job is still in its backoff window."""
        entry = cls._entries.get(job_id)
        if entry is None:
            return False
        return now < entry.next_try

    @classmethod
    def record_failure(cls, job_id: str, now: datetime.datetime) -> None:
        """Record an unschedulable attempt and compute next retry time."""
        base_delay, max_delay, _max_attempts = cls._resolve()
        entry = cls._entries.get(job_id)
        if entry is None:
            entry = _BackoffEntry()
            cls._entries[job_id] = entry
        entry.attempts += 1
        from backend.core.scheduling_policy_store import get_policy_store
        _max_exp = get_policy_store().active.backoff.max_exponent
        exp = min(entry.attempts - 1, _max_exp)  # cap exponent to avoid overflow
        delay = min(base_delay * (2 ** exp), max_delay)
        entry.next_try = now + datetime.timedelta(seconds=delay)

        # Periodic cleanup of stale entries
        cls._call_counter += 1
        if cls._call_counter % cls._CLEANUP_INTERVAL == 0:
            cls._cleanup(now)

    @classmethod
    def record_success(cls, job_id: str) -> None:
        """Clear backoff state on successful scheduling."""
        cls._entries.pop(job_id, None)

    @classmethod
    def get_info(cls, job_id: str) -> tuple[int, datetime.datetime | None]:
        """Return (attempts, next_try) for diagnostics."""
        entry = cls._entries.get(job_id)
        if entry is None:
            return 0, None
        return entry.attempts, entry.next_try

    @classmethod
    def _cleanup(cls, now: datetime.datetime) -> None:
        """Remove entries whose backoff has long expired (>2× max_delay)."""
        _, max_delay, _ = cls._resolve()
        threshold = now - datetime.timedelta(seconds=max_delay * 2)
        stale = [k for k, v in cls._entries.items() if v.next_try < threshold]
        for k in stale:
            del cls._entries[k]

    @classmethod
    def reset(cls) -> None:
        cls._entries.clear()
        cls._call_counter = 0


# =====================================================================
# 4. Admission Controller  (K8s ResourceQuota / Nomad job-quota)
# =====================================================================


class AdmissionController:
    """Queue depth backpressure — reject new submissions when saturated.

    Prevents unbounded queue growth that degrades scheduling latency
    for all tenants.  The ``check_admission`` method is called from
    ``create_job()`` before persisting the new job.

    Kubernetes equivalent: ``ResourceQuota`` (count/pods limit).
    Nomad equivalent: ``job-quota`` stanza.
    """

    DEFAULT_MAX_PENDING_PER_TENANT: int | None = None
    DEFAULT_MAX_TOTAL_ACTIVE: int | None = None

    @classmethod
    def _resolve_max_pending(cls) -> int:
        if cls.DEFAULT_MAX_PENDING_PER_TENANT is not None:
            return cls.DEFAULT_MAX_PENDING_PER_TENANT
        from backend.core.scheduling_policy_store import get_policy_store
        return get_policy_store().active.admission.max_pending_per_tenant

    @staticmethod
    async def check_admission(
        db: AsyncSession,
        tenant_id: str,
        *,
        max_pending: int | None = None,
    ) -> tuple[bool, str, dict[str, int]]:
        """Check whether the tenant can submit a new job.

        Returns (admitted, reason, details).
        """
        from backend.models.job import Job

        limit = max_pending or AdmissionController._resolve_max_pending()
        result = await db.execute(
            select(func.count()).where(
                Job.tenant_id == tenant_id,
                Job.status.in_(["pending", "leased"]),
            )
        )
        count = result.scalar() or 0
        if count >= limit:
            return (
                False,
                f"queue_depth_exceeded: {count}/{limit} active jobs for tenant",
                {"current": count, "limit": limit, "tenant_id": tenant_id},
            )
        return True, "", {"current": count, "limit": limit}


# =====================================================================
# 5. Scheduling Metrics  (K8s scheduler_framework_extension_point)
# =====================================================================


@dataclass(frozen=True)
class _MetricEvent:
    ts: float  # time.monotonic()
    wall_ts: datetime.datetime


class SchedulingMetrics:
    """Lightweight in-memory scheduling throughput & latency tracker.

    Not a replacement for Prometheus — this provides an instant snapshot
    for the ``/api/v1/console/diagnostics`` and ``/api/v1/jobs/explain``
    endpoints without external dependencies.

    Kubernetes equivalent: ``scheduler_scheduling_attempt_total``,
    ``scheduler_scheduling_duration_seconds``.
    """

    _MAX_EVENTS = 5000
    _placements: deque[tuple[float, float]] = deque(maxlen=_MAX_EVENTS)
    _rejections: deque[tuple[float, str]] = deque(maxlen=_MAX_EVENTS)
    _durations_ms: deque[float] = deque(maxlen=_MAX_EVENTS)
    _backoff_skips: int = 0
    _admission_rejections: int = 0
    _preemption_budget_hits: int = 0

    @classmethod
    def record_placement(cls, duration_ms: float) -> None:
        now = time.monotonic()
        cls._placements.append((now, duration_ms))
        cls._durations_ms.append(duration_ms)

    @classmethod
    def record_rejection(cls, reason: str) -> None:
        cls._rejections.append((time.monotonic(), reason))

    @classmethod
    def record_backoff_skip(cls) -> None:
        cls._backoff_skips += 1

    @classmethod
    def record_admission_rejection(cls) -> None:
        cls._admission_rejections += 1

    @classmethod
    def record_preemption_budget_hit(cls) -> None:
        cls._preemption_budget_hits += 1

    @classmethod
    def snapshot(cls, window_seconds: float = 300) -> dict[str, object]:
        """Return aggregate metrics for the given time window."""
        cutoff = time.monotonic() - window_seconds
        recent_p = [(t, d) for t, d in cls._placements if t > cutoff]
        recent_r = [(t, r) for t, r in cls._rejections if t > cutoff]
        recent_d = [d for t, d in cls._placements if t > cutoff]
        minutes = window_seconds / 60.0

        avg_latency = sum(recent_d) / len(recent_d) if recent_d else 0.0
        p95_latency = sorted(recent_d)[int(len(recent_d) * 0.95)] if recent_d else 0.0
        total_attempts = len(recent_p) + len(recent_r)
        rejection_rate = len(recent_r) / total_attempts if total_attempts else 0.0

        reason_counts = Counter(r for _, r in recent_r)
        top_reasons = dict(reason_counts.most_common(10))

        return {
            "window_seconds": window_seconds,
            "placements": len(recent_p),
            "rejections": len(recent_r),
            "placements_per_minute": round(len(recent_p) / minutes, 2) if minutes else 0,
            "avg_scheduling_latency_ms": round(avg_latency, 2),
            "p95_scheduling_latency_ms": round(p95_latency, 2),
            "rejection_rate": round(rejection_rate, 4),
            "top_rejection_reasons": top_reasons,
            "backoff_skips_total": cls._backoff_skips,
            "admission_rejections_total": cls._admission_rejections,
            "preemption_budget_hits_total": cls._preemption_budget_hits,
        }

    @classmethod
    def reset(cls) -> None:
        cls._placements.clear()
        cls._rejections.clear()
        cls._durations_ms.clear()
        cls._backoff_skips = 0
        cls._admission_rejections = 0
        cls._preemption_budget_hits = 0
