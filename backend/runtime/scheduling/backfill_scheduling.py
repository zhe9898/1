"""Backfill & Reservation Scheduling 閳?time-dimension resource planning.

Adds two capabilities that mature batch schedulers (Slurm, PBS, Moab)
consider essential for mixed-workload clusters:

1. **Reservation** 閳?guarantee a future start window for high-priority
   jobs by blocking the required resources on specific nodes.
2. **Backfill** 閳?fill idle gaps with smaller/shorter jobs that will
   complete *before* any reservation begins, maximising utilisation
   without delaying reserved work.

Design principles:
- **Non-invasive**: Works alongside the existing pull-based dispatch model.
  Reservations are advisory hints that the scoring/acceptance pipeline
  respects, not a separate scheduling path.
- **Time-aware**: Uses ``estimated_duration_s`` (already on the Job model)
  to predict completion times and enforce reservation windows.
- **Configurable**: All thresholds are in ``BackfillConfig`` (frozen
  dataclass), exposed through the policy store.
- **Pluggable storage**: ``ReservationStore`` protocol with in-memory
  (single-gateway) and Redis (multi-gateway) implementations.

References:
- Slurm ``sched/backfill`` plugin
- PBS Pro ``backfill_depth``
- Moab ``BACKFILLPOLICY``
- K8s Kueue ``preemption.borrowWithinCohort``
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.runtime.scheduling.job_scheduler import SchedulerNodeSnapshot
    from backend.models.job import Job

from backend.runtime.scheduling.reservation_models import ResourceReservation, resolve_reservation_tenant_id
from backend.runtime.scheduling.reservation_store import (
    InMemoryReservationStore,
    ReservationQuery,
    ReservationStore,
)
from backend.runtime.scheduling.reservation_store_factory import build_reservation_store_from_env
from backend.runtime.scheduling.scheduling_constraints import SchedulingConstraint, SchedulingContext

logger = logging.getLogger(__name__)


def _planning_horizon(now: datetime.datetime, config: BackfillConfig) -> datetime.datetime:
    return now + datetime.timedelta(seconds=config.planning_horizon_s)


def _gap_duration_seconds(start_at: datetime.datetime, end_at: datetime.datetime) -> float:
    return (end_at - start_at).total_seconds()


def _gap_supports_backfill(
    start_at: datetime.datetime,
    end_at: datetime.datetime,
    *,
    required_duration_s: int,
    min_gap_s: int,
) -> bool:
    gap_seconds = _gap_duration_seconds(start_at, end_at)
    return gap_seconds >= required_duration_s and gap_seconds >= min_gap_s


def _reservation_duration_seconds(
    job: Job,
    config: BackfillConfig,
    *,
    estimated_duration_s: int | None = None,
) -> int:
    return estimated_duration_s or getattr(job, "estimated_duration_s", None) or config.default_estimated_duration_s


def _build_reservation_record(
    job: Job,
    node: SchedulerNodeSnapshot,
    *,
    start_at: datetime.datetime,
    duration_seconds: int,
) -> ResourceReservation:
    return ResourceReservation(
        job_id=job.job_id,
        tenant_id=resolve_reservation_tenant_id(getattr(job, "tenant_id", "default")),
        node_id=node.node_id,
        start_at=start_at,
        end_at=start_at + datetime.timedelta(seconds=duration_seconds),
        priority=int(job.priority or 50),
        cpu_cores=max(float(getattr(job, "required_cpu_cores", 0) or 0), 0.0),
        memory_mb=max(float(getattr(job, "required_memory_mb", 0) or 0), 0.0),
        gpu_vram_mb=max(float(getattr(job, "required_gpu_vram_mb", 0) or 0), 0.0),
    )


def _reservation_windows(
    reservations: list[ResourceReservation],
    *,
    now: datetime.datetime,
    horizon: datetime.datetime,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    if not reservations:
        return [(now, horizon)]

    windows: list[tuple[datetime.datetime, datetime.datetime]] = []
    first_start = reservations[0].start_at
    if now < first_start:
        windows.append((now, first_start))

    for current, following in zip(reservations, reservations[1:], strict=False):
        windows.append((current.end_at, following.start_at))

    last_end = reservations[-1].end_at
    if last_end < horizon:
        windows.append((last_end, horizon))
    return windows


# =====================================================================
# Configuration
# =====================================================================


@dataclass(frozen=True, slots=True)
class BackfillConfig:
    """Backfill scheduling parameters."""

    enabled: bool = True
    # Max number of reservations maintained concurrently
    max_reservations: int = 50
    # Default estimated duration if job doesn't declare one (seconds)
    default_estimated_duration_s: int = 300
    # Only backfill jobs shorter than this (seconds, 0 = no limit)
    max_backfill_duration_s: int = 0
    # How far ahead to look for reservation windows (seconds)
    planning_horizon_s: int = 3600
    # Minimum gap that can be backfilled (seconds)
    min_gap_s: int = 30
    # Priority threshold 閳?only jobs >= this get reservations
    reservation_min_priority: int = 70


# Reservation models and store adapters live in reservation_models.py
# and reservation_store.py so this module can stay focused on policy.

# =====================================================================
# Reservation Manager 閳?maintains the reservation table
# =====================================================================


class ReservationManager:
    """Reservation table backed by a pluggable ``ReservationStore``.

    For single-gateway deployments the default ``InMemoryReservationStore``
    is used.  Multi-gateway clusters should configure
    ``scheduling.reservation_store: redis`` in ``system.yaml`` to enable
    the ``RedisReservationStore``.
    """

    def __init__(
        self,
        config: BackfillConfig | None = None,
        store: ReservationStore | None = None,
    ) -> None:
        self._config = config or BackfillConfig()
        self._store = store or InMemoryReservationStore(self._config.max_reservations)

    @property
    def config(self) -> BackfillConfig:
        return self._config

    def create_reservation(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        start_at: datetime.datetime,
        estimated_duration_s: int | None = None,
    ) -> ResourceReservation | None:
        """Create a reservation for a high-priority job on a node.

        Returns the reservation, or None if max capacity reached.
        """
        existing = self._store.get(job.job_id)
        if existing is not None:
            return existing

        duration = _reservation_duration_seconds(
            job,
            self._config,
            estimated_duration_s=estimated_duration_s,
        )
        reservation = _build_reservation_record(
            job,
            node,
            start_at=start_at,
            duration_seconds=duration,
        )

        if not self._store.put(reservation):
            logger.debug("reservation_table_full: count=%d", self._store.count())
            return None

        logger.info(
            "reservation_created: job=%s node=%s start=%s end=%s",
            job.job_id,
            node.node_id,
            start_at.isoformat(),
            reservation.end_at.isoformat(),
        )
        return reservation

    def cancel_reservation(self, job_id: str) -> bool:
        """Cancel a reservation (job was scheduled, cancelled, or expired)."""
        return self._store.remove(job_id) is not None

    def get_node_reservations(
        self,
        node_id: str,
        *,
        tenant_id: str = "default",
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        """Get active reservations for a node, optionally filtered by time."""
        return self._store.get_by_node(node_id, tenant_id=tenant_id, after=after)

    def list_reservations(
        self,
        *,
        tenant_id: str | None = None,
        node_id: str | None = None,
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        """List reservations with optional tenant/node/time filtering."""
        return self._store.list(ReservationQuery(tenant_id=tenant_id, node_id=node_id, after=after))

    def get_reservation(self, job_id: str) -> ResourceReservation | None:
        """Get a specific job's reservation."""
        return self._store.get(job_id)

    def cleanup_expired(self, now: datetime.datetime) -> int:
        """Remove expired reservations. Returns count removed."""
        return self._store.cleanup_expired(now)

    @property
    def reservation_count(self) -> int:
        return self._store.count()

    @property
    def store_backend(self) -> str:
        return self._store.backend_name

    def find_backfill_window(
        self,
        node: SchedulerNodeSnapshot,
        *,
        tenant_id: str | None = None,
        now: datetime.datetime,
        required_duration_s: int,
    ) -> tuple[datetime.datetime, datetime.datetime] | None:
        """Find the earliest gap on a node where a job of given duration fits.

        Returns (gap_start, gap_end) or None if no suitable gap exists.
        """
        resolved_tenant_id = tenant_id or resolve_reservation_tenant_id(getattr(node, "tenant_id", "default"))
        reservations = self.get_node_reservations(node.node_id, tenant_id=resolved_tenant_id, after=now)

        if not reservations:
            # No reservations 閳?entire horizon is open
            horizon = now + datetime.timedelta(seconds=self._config.planning_horizon_s)
            return (now, horizon)

        # Check gap before first reservation
        first_start = reservations[0].start_at
        gap = (first_start - now).total_seconds()
        if gap >= required_duration_s and gap >= self._config.min_gap_s:
            return (now, first_start)

        # Check gaps between reservations
        for i in range(len(reservations) - 1):
            gap_start = reservations[i].end_at
            gap_end = reservations[i + 1].start_at
            gap = (gap_end - gap_start).total_seconds()
            if gap >= required_duration_s and gap >= self._config.min_gap_s:
                return (gap_start, gap_end)

        # Check gap after last reservation
        last_end = reservations[-1].end_at
        horizon = now + datetime.timedelta(seconds=self._config.planning_horizon_s)
        if last_end < horizon:
            gap = (horizon - last_end).total_seconds()
            if gap >= required_duration_s and gap >= self._config.min_gap_s:
                return (last_end, horizon)

        return None


# =====================================================================
# Backfill Evaluator 閳?determines which jobs can backfill
# =====================================================================


class BackfillEvaluator:
    """Evaluate whether a job can be scheduled as a backfill candidate.

    A job is backfill-eligible if:
    1. It has ``estimated_duration_s`` set (or uses the default).
    2. It will complete before any reservation on the target node begins.
    3. Its duration doesn't exceed ``max_backfill_duration_s`` (if set).
    """

    def __init__(self, reservation_mgr: ReservationManager) -> None:
        self._reservation_mgr = reservation_mgr

    def can_backfill(
        self,
        job: Job,
        node: SchedulerNodeSnapshot,
        *,
        now: datetime.datetime,
    ) -> tuple[bool, str]:
        """Check if job can be placed as a backfill on this node.

        Returns (can_backfill, reason).
        """
        cfg = self._reservation_mgr.config
        if not cfg.enabled:
            return True, "backfill_disabled"

        duration = getattr(job, "estimated_duration_s", None) or cfg.default_estimated_duration_s

        # Check max backfill duration
        if cfg.max_backfill_duration_s > 0 and duration > cfg.max_backfill_duration_s:
            return False, f"duration_exceeds_limit:{duration}>{cfg.max_backfill_duration_s}"

        # Check against node reservations
        estimated_end = now + datetime.timedelta(seconds=duration)
        tenant_id = resolve_reservation_tenant_id(getattr(job, "tenant_id", None))
        if tenant_id == "default":
            tenant_id = resolve_reservation_tenant_id(getattr(node, "tenant_id", "default"))
        reservations = self._reservation_mgr.get_node_reservations(node.node_id, tenant_id=tenant_id, after=now)

        for r in reservations:
            if r.job_id == job.job_id:
                continue  # This job's own reservation
            if estimated_end > r.start_at:
                return False, f"would_delay_reservation:{r.job_id}:starts_at:{r.start_at.isoformat()}"

        return True, "ok"


# =====================================================================
# Scheduling Constraints 閳?integrate with the constraint pipeline
# =====================================================================


class ReservationHonorGate(SchedulingConstraint):
    """Soft constraint that boosts jobs with active reservations.

    If a job has a reservation and the dispatch is happening within
    or near the reservation window, give it a priority boost to ensure
    it gets scheduled as planned.
    """

    name = "reservation_honor"
    order = 6  # Very early, after resource_quota (5)
    hard = False

    def __init__(self, reservation_mgr: ReservationManager) -> None:
        self._mgr = reservation_mgr

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        reservation = self._mgr.get_reservation(job.job_id)
        if reservation is None:
            return True, ""

        # Read boost values from the policy store
        try:
            from backend.kernel.policy.policy_store import get_policy_store

            bp = get_policy_store().active.backfill
            imminent_boost = bp.reservation_imminent_boost
            imminent_window = bp.reservation_imminent_window_s
            approaching_boost = bp.reservation_approaching_boost
            approaching_window = bp.reservation_approaching_window_s
            priority_cap = 160
        except Exception:
            imminent_boost = 30
            imminent_window = 60
            approaching_boost = 15
            approaching_window = 300
            priority_cap = 160

        # If within or past reservation start, boost priority significantly
        time_to_start = (reservation.start_at - ctx.now).total_seconds()
        if time_to_start <= 0:
            current_pri = int(job.priority or 50)
            job.priority = priority_cap
            return True, f"reservation_due:max_priority:+{priority_cap - current_pri}:time_to_start:{time_to_start:.0f}s"
        if time_to_start <= imminent_window:
            boost = imminent_boost
        elif time_to_start <= approaching_window:
            boost = approaching_boost
        else:
            boost = 0

        if boost > 0:
            current_pri = int(job.priority or 50)
            job.priority = min(priority_cap, current_pri + boost)
            return True, f"reservation_boost:{boost}:time_to_start:{time_to_start:.0f}s"

        return True, ""


class BackfillGate(SchedulingConstraint):
    """Soft constraint that identifies backfill-eligible low-priority jobs.

    For jobs below the reservation priority threshold, marks them as
    backfill candidates in ``ctx.data["_backfill_eligible"]`` so the
    scoring layer can apply appropriate bonuses.
    """

    name = "backfill_marker"
    order = 45  # After priority boost (40)
    hard = False

    def __init__(self, reservation_mgr: ReservationManager) -> None:
        self._mgr = reservation_mgr

    def evaluate(self, job: Job, ctx: SchedulingContext) -> tuple[bool, str]:
        cfg = self._mgr.config
        if not cfg.enabled:
            return True, ""

        priority = int(job.priority or 50)
        if priority >= cfg.reservation_min_priority:
            return True, ""  # High priority 閳?not a backfill candidate

        # Mark as backfill-eligible for downstream scoring
        eligible_set: set[str] = ctx.data.setdefault("_backfill_eligible", set())  # type: ignore[assignment]
        eligible_set.add(job.job_id)
        return True, "backfill_candidate"


# =====================================================================
# Module-level singleton
# =====================================================================

_reservation_manager: ReservationManager | None = None


def get_reservation_manager() -> ReservationManager:
    """Return the process-wide ReservationManager singleton.

    Configuration is read from the policy store's ``backfill`` section.
    Store backend selection is delegated to the reservation store factory,
    which reads the runtime environment at the adapter edge.
    """
    global _reservation_manager
    if _reservation_manager is None:
        try:
            from backend.kernel.policy.policy_store import get_policy_store

            bp = get_policy_store().active.backfill
            config = BackfillConfig(
                enabled=bp.enabled,
                max_reservations=bp.max_reservations,
                default_estimated_duration_s=bp.default_estimated_duration_s,
                max_backfill_duration_s=bp.max_backfill_duration_s,
                planning_horizon_s=bp.planning_horizon_s,
                min_gap_s=bp.min_gap_s,
                reservation_min_priority=bp.reservation_min_priority,
            )
        except Exception:
            config = BackfillConfig()

        # Select store backend from the runtime environment at the adapter edge.
        store = build_reservation_store_from_env(
            max_reservations=config.max_reservations,
            logger=logger,
        )

        _reservation_manager = ReservationManager(config, store=store)
    return _reservation_manager


def reset_reservation_manager() -> None:
    """Reset singleton (for tests)."""
    global _reservation_manager
    _reservation_manager = None

