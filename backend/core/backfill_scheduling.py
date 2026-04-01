"""Backfill & Reservation Scheduling — time-dimension resource planning.

Adds two capabilities that mature batch schedulers (Slurm, PBS, Moab)
consider essential for mixed-workload clusters:

1. **Reservation** — guarantee a future start window for high-priority
   jobs by blocking the required resources on specific nodes.
2. **Backfill** — fill idle gaps with smaller/shorter jobs that will
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
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.job_scheduler import SchedulerNodeSnapshot
    from backend.models.job import Job

from backend.core.scheduling_constraints import SchedulingConstraint, SchedulingContext

logger = logging.getLogger(__name__)


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
    # Priority threshold — only jobs >= this get reservations
    reservation_min_priority: int = 70


# =====================================================================
# Reservation data structures
# =====================================================================


@dataclass(slots=True)
class ResourceReservation:
    """A reservation of resources on a specific node for a future job.

    The reservation guarantees that between ``start_at`` and ``end_at``,
    the reserved resource dimensions are held for ``job_id``.
    """

    job_id: str
    node_id: str
    start_at: datetime.datetime
    end_at: datetime.datetime
    priority: int
    # Reserved resource dimensions
    cpu_cores: float = 0.0
    memory_mb: float = 0.0
    gpu_vram_mb: float = 0.0
    slots: int = 1

    def overlaps(self, start: datetime.datetime, end: datetime.datetime) -> bool:
        """Check if a time window overlaps with this reservation."""
        return start < self.end_at and end > self.start_at

    def is_expired(self, now: datetime.datetime) -> bool:
        """Check if the reservation window has passed."""
        return now >= self.end_at

    def resource_conflicts(
        self,
        cpu: float = 0.0,
        memory: float = 0.0,
        gpu: float = 0.0,
    ) -> bool:
        """Check if requesting these resources would conflict."""
        return (self.cpu_cores > 0 and cpu > 0) or (self.memory_mb > 0 and memory > 0) or (self.gpu_vram_mb > 0 and gpu > 0)

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe dict (for Redis store)."""
        return {
            "job_id": self.job_id,
            "node_id": self.node_id,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "priority": self.priority,
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "gpu_vram_mb": self.gpu_vram_mb,
            "slots": self.slots,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ResourceReservation:
        """Deserialise from a JSON dict."""
        return cls(
            job_id=str(data["job_id"]),
            node_id=str(data["node_id"]),
            start_at=datetime.datetime.fromisoformat(str(data["start_at"])),
            end_at=datetime.datetime.fromisoformat(str(data["end_at"])),
            priority=int(data.get("priority", 50)),  # type: ignore[arg-type]
            cpu_cores=float(data.get("cpu_cores", 0.0)),  # type: ignore[arg-type]
            memory_mb=float(data.get("memory_mb", 0.0)),  # type: ignore[arg-type]
            gpu_vram_mb=float(data.get("gpu_vram_mb", 0.0)),  # type: ignore[arg-type]
            slots=int(data.get("slots", 1)),  # type: ignore[arg-type]
        )


# =====================================================================
# ReservationStore — pluggable storage backend
# =====================================================================


class ReservationStore(ABC):
    """Abstract interface for reservation persistence.

    Implementations must be safe for single-threaded async usage.
    """

    @abstractmethod
    def put(self, reservation: ResourceReservation) -> bool:
        """Store a reservation. Returns False if max capacity reached."""
        ...

    @abstractmethod
    def remove(self, job_id: str) -> ResourceReservation | None:
        """Remove and return a reservation by job_id."""
        ...

    @abstractmethod
    def get(self, job_id: str) -> ResourceReservation | None:
        """Retrieve a reservation by job_id."""
        ...

    @abstractmethod
    def get_by_node(self, node_id: str, *, after: datetime.datetime | None = None) -> list[ResourceReservation]:
        """Get reservations for a node, optionally filtered by time."""
        ...

    @abstractmethod
    def count(self) -> int:
        """Total number of active reservations."""
        ...

    @abstractmethod
    def cleanup_expired(self, now: datetime.datetime) -> int:
        """Remove expired reservations. Returns count removed."""
        ...


class InMemoryReservationStore(ReservationStore):
    """Process-local dict-based store (single-gateway deployments)."""

    def __init__(self, max_reservations: int = 50) -> None:
        self._max = max_reservations
        self._reservations: dict[str, ResourceReservation] = {}
        self._node_index: dict[str, list[str]] = {}

    def put(self, reservation: ResourceReservation) -> bool:
        if reservation.job_id in self._reservations:
            return True  # idempotent
        if len(self._reservations) >= self._max:
            return False
        self._reservations[reservation.job_id] = reservation
        self._node_index.setdefault(reservation.node_id, []).append(reservation.job_id)
        return True

    def remove(self, job_id: str) -> ResourceReservation | None:
        r = self._reservations.pop(job_id, None)
        if r is not None:
            nlist = self._node_index.get(r.node_id, [])
            if job_id in nlist:
                nlist.remove(job_id)
        return r

    def get(self, job_id: str) -> ResourceReservation | None:
        return self._reservations.get(job_id)

    def get_by_node(self, node_id: str, *, after: datetime.datetime | None = None) -> list[ResourceReservation]:
        jids = self._node_index.get(node_id, [])
        result = [self._reservations[jid] for jid in jids if jid in self._reservations]
        if after is not None:
            result = [r for r in result if r.end_at > after]
        return sorted(result, key=lambda r: r.start_at)

    def count(self) -> int:
        return len(self._reservations)

    def cleanup_expired(self, now: datetime.datetime) -> int:
        expired = [jid for jid, r in self._reservations.items() if r.is_expired(now)]
        for jid in expired:
            self.remove(jid)
        return len(expired)


class RedisReservationStore(ReservationStore):
    """Redis-backed distributed store for multi-gateway deployments.

    Storage schema:
    - ``zen70:reservations:data:{job_id}`` — JSON hash of reservation
    - ``zen70:reservations:node:{node_id}`` — sorted set (score = start_at epoch)
    - ``zen70:reservations:count`` — atomic counter (approximation)
    """

    _PREFIX = "zen70:reservations"

    def __init__(self, redis_client: object, max_reservations: int = 50) -> None:
        self._redis = redis_client  # redis.Redis compatible
        self._max = max_reservations

    def _data_key(self, job_id: str) -> str:
        return f"{self._PREFIX}:data:{job_id}"

    def _node_key(self, node_id: str) -> str:
        return f"{self._PREFIX}:node:{node_id}"

    def _count_key(self) -> str:
        return f"{self._PREFIX}:count"

    def put(self, reservation: ResourceReservation) -> bool:
        r = self._redis  # type: ignore[union-attr]
        # Check current count (approximate — race is acceptable for capacity limit)
        current = int(r.get(self._count_key()) or 0)
        # Idempotent: if already exists, just return True
        if r.exists(self._data_key(reservation.job_id)):
            return True
        if current >= self._max:
            return False

        pipe = r.pipeline(transaction=True)
        pipe.set(
            self._data_key(reservation.job_id),
            json.dumps(reservation.to_dict()),
            ex=max(int((reservation.end_at - datetime.datetime.now(datetime.UTC)).total_seconds()), 60),
        )
        pipe.zadd(
            self._node_key(reservation.node_id),
            {reservation.job_id: reservation.start_at.timestamp()},
        )
        pipe.incr(self._count_key())
        pipe.execute()
        return True

    def remove(self, job_id: str) -> ResourceReservation | None:
        r = self._redis  # type: ignore[union-attr]
        raw = r.get(self._data_key(job_id))
        if raw is None:
            return None
        data = json.loads(raw)
        reservation = ResourceReservation.from_dict(data)
        pipe = r.pipeline(transaction=True)
        pipe.delete(self._data_key(job_id))
        pipe.zrem(self._node_key(reservation.node_id), job_id)
        pipe.decr(self._count_key())
        pipe.execute()
        return reservation

    def get(self, job_id: str) -> ResourceReservation | None:
        r = self._redis  # type: ignore[union-attr]
        raw = r.get(self._data_key(job_id))
        if raw is None:
            return None
        return ResourceReservation.from_dict(json.loads(raw))

    def get_by_node(self, node_id: str, *, after: datetime.datetime | None = None) -> list[ResourceReservation]:
        r = self._redis  # type: ignore[union-attr]
        min_score = after.timestamp() if after else "-inf"
        jids = r.zrangebyscore(self._node_key(node_id), min_score, "+inf")
        result: list[ResourceReservation] = []
        for jid_bytes in jids:
            jid = jid_bytes.decode() if isinstance(jid_bytes, bytes) else str(jid_bytes)
            raw = r.get(self._data_key(jid))
            if raw is None:
                r.zrem(self._node_key(node_id), jid)  # stale index entry
                continue
            reservation = ResourceReservation.from_dict(json.loads(raw))
            if after is None or reservation.end_at > after:
                result.append(reservation)
        return sorted(result, key=lambda rv: rv.start_at)

    def count(self) -> int:
        r = self._redis  # type: ignore[union-attr]
        return max(int(r.get(self._count_key()) or 0), 0)

    def cleanup_expired(self, now: datetime.datetime) -> int:
        # Redis TTL on data keys handles most cleanup automatically.
        # This method handles node index housekeeping.
        r = self._redis  # type: ignore[union-attr]
        removed = 0
        # Scan node keys and remove entries whose data has expired
        cursor = 0
        node_prefix = f"{self._PREFIX}:node:"
        while True:
            cursor, keys = r.scan(cursor, match=f"{node_prefix}*", count=100)
            for nkey in keys:
                members = r.zrangebyscore(nkey, "-inf", "+inf")
                for jid_bytes in members:
                    jid = jid_bytes.decode() if isinstance(jid_bytes, bytes) else str(jid_bytes)
                    if not r.exists(self._data_key(jid)):
                        r.zrem(nkey, jid)
                        r.decr(self._count_key())
                        removed += 1
            if cursor == 0:
                break
        return removed


# =====================================================================
# Reservation Manager — maintains the reservation table
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

        duration = estimated_duration_s or getattr(job, "estimated_duration_s", None) or self._config.default_estimated_duration_s
        end_at = start_at + datetime.timedelta(seconds=duration)

        reservation = ResourceReservation(
            job_id=job.job_id,
            node_id=node.node_id,
            start_at=start_at,
            end_at=end_at,
            priority=int(job.priority or 50),
            cpu_cores=max(float(getattr(job, "required_cpu_cores", 0) or 0), 0.0),
            memory_mb=max(float(getattr(job, "required_memory_mb", 0) or 0), 0.0),
            gpu_vram_mb=max(float(getattr(job, "required_gpu_vram_mb", 0) or 0), 0.0),
        )

        if not self._store.put(reservation):
            logger.debug("reservation_table_full: count=%d", self._store.count())
            return None

        logger.info(
            "reservation_created: job=%s node=%s start=%s end=%s",
            job.job_id,
            node.node_id,
            start_at.isoformat(),
            end_at.isoformat(),
        )
        return reservation

    def cancel_reservation(self, job_id: str) -> bool:
        """Cancel a reservation (job was scheduled, cancelled, or expired)."""
        return self._store.remove(job_id) is not None

    def get_node_reservations(
        self,
        node_id: str,
        *,
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        """Get active reservations for a node, optionally filtered by time."""
        return self._store.get_by_node(node_id, after=after)

    def get_reservation(self, job_id: str) -> ResourceReservation | None:
        """Get a specific job's reservation."""
        return self._store.get(job_id)

    def cleanup_expired(self, now: datetime.datetime) -> int:
        """Remove expired reservations. Returns count removed."""
        return self._store.cleanup_expired(now)

    @property
    def reservation_count(self) -> int:
        return self._store.count()

    def find_backfill_window(
        self,
        node: SchedulerNodeSnapshot,
        *,
        now: datetime.datetime,
        required_duration_s: int,
    ) -> tuple[datetime.datetime, datetime.datetime] | None:
        """Find the earliest gap on a node where a job of given duration fits.

        Returns (gap_start, gap_end) or None if no suitable gap exists.
        """
        reservations = self.get_node_reservations(node.node_id, after=now)

        if not reservations:
            # No reservations — entire horizon is open
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
# Backfill Evaluator — determines which jobs can backfill
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
        reservations = self._reservation_mgr.get_node_reservations(node.node_id, after=now)

        for r in reservations:
            if r.job_id == job.job_id:
                continue  # This job's own reservation
            if estimated_end > r.start_at:
                return False, f"would_delay_reservation:{r.job_id}:starts_at:{r.start_at.isoformat()}"

        return True, "ok"


# =====================================================================
# Scheduling Constraints — integrate with the constraint pipeline
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
            from backend.core.scheduling_policy_store import get_policy_store

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
            return True, ""  # High priority — not a backfill candidate

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
    If ``scheduling.reservation_store`` is ``"redis"`` in ``system.yaml``,
    a ``RedisReservationStore`` is used; otherwise defaults to in-memory.
    """
    global _reservation_manager
    if _reservation_manager is None:
        try:
            from backend.core.scheduling_policy_store import get_policy_store

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

        # Select store backend
        store: ReservationStore | None = None
        try:
            from backend.core.config import get_config

            store_type = get_config().get("scheduling", {}).get("reservation_store", "memory")
            if store_type == "redis":
                import redis as _redis_mod

                redis_url = get_config().get("scheduling", {}).get("reservation_store_redis_url", "redis://localhost:6379/0")
                redis_client = _redis_mod.Redis.from_url(redis_url, decode_responses=False)
                store = RedisReservationStore(redis_client, config.max_reservations)
                logger.info("reservation_store=redis url=%s", redis_url)
        except Exception:
            logger.debug("reservation_store=memory (redis not available or not configured)")

        _reservation_manager = ReservationManager(config, store=store)
    return _reservation_manager


def reset_reservation_manager() -> None:
    """Reset singleton (for tests)."""
    global _reservation_manager
    _reservation_manager = None
