"""Backfill & Reservation Scheduling 鈥?time-dimension resource planning.

Adds two capabilities that mature batch schedulers (Slurm, PBS, Moab)
consider essential for mixed-workload clusters:

1. **Reservation** 鈥?guarantee a future start window for high-priority
   jobs by blocking the required resources on specific nodes.
2. **Backfill** 鈥?fill idle gaps with smaller/shorter jobs that will
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
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from backend.kernel.scheduling.job_scheduler import SchedulerNodeSnapshot
    from backend.models.job import Job

from backend.kernel.scheduling.scheduling_constraints import SchedulingConstraint, SchedulingContext
from backend.platform.redis import SyncRedisClient

logger = logging.getLogger(__name__)


def _resolve_tenant_id(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return "default"


def _reservation_store_settings() -> tuple[str, str]:
    store_type = str(os.getenv("ZEN70_RESERVATION_STORE", "memory")).strip().lower() or "memory"
    redis_url = str(os.getenv("ZEN70_RESERVATION_STORE_REDIS_URL", "redis://localhost:6379/0")).strip()
    return store_type, redis_url


def _build_reservation_store(config: BackfillConfig) -> ReservationStore | None:
    store_type, redis_url = _reservation_store_settings()
    if store_type == "memory":
        return None
    if store_type != "redis":
        raise RuntimeError(f"ZEN-BACKFILL-STORE-INVALID: unsupported reservation store '{store_type}'")

    parsed = urlparse(redis_url)
    redis_client = SyncRedisClient(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        password=parsed.password,
        db=int((parsed.path or "/0").lstrip("/") or "0"),
        username=parsed.username,
    )
    try:
        redis_client.connect()
    except Exception as exc:
        raise RuntimeError(f"ZEN-BACKFILL-STORE-UNAVAILABLE: reservation_store=redis but Redis initialization failed for {redis_url}") from exc
    logger.info("reservation_store=redis url=%s", redis_url)
    return RedisReservationStore(redis_client, config.max_reservations)


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
    # Priority threshold 鈥?only jobs >= this get reservations
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
    tenant_id: str = "default"

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
            "tenant_id": self.tenant_id,
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
            tenant_id=str(data.get("tenant_id", "default")),
            node_id=str(data["node_id"]),
            start_at=datetime.datetime.fromisoformat(str(data["start_at"])),
            end_at=datetime.datetime.fromisoformat(str(data["end_at"])),
            priority=int(str(data.get("priority", 50))),
            cpu_cores=float(str(data.get("cpu_cores", 0.0))),
            memory_mb=float(str(data.get("memory_mb", 0.0))),
            gpu_vram_mb=float(str(data.get("gpu_vram_mb", 0.0))),
            slots=int(str(data.get("slots", 1))),
        )


# =====================================================================
# ReservationStore 鈥?pluggable storage backend
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
    def get_by_node(
        self,
        node_id: str,
        *,
        tenant_id: str = "default",
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        """Get reservations for a node, optionally filtered by time."""
        ...

    @abstractmethod
    def list(
        self,
        *,
        tenant_id: str | None = None,
        node_id: str | None = None,
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        """List reservations with optional tenant/node/time filtering."""
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
        self._node_index: dict[tuple[str, str], list[str]] = {}

    def put(self, reservation: ResourceReservation) -> bool:
        if reservation.job_id in self._reservations:
            return True  # idempotent
        if len(self._reservations) >= self._max:
            return False
        self._reservations[reservation.job_id] = reservation
        self._node_index.setdefault((reservation.tenant_id, reservation.node_id), []).append(reservation.job_id)
        return True

    def remove(self, job_id: str) -> ResourceReservation | None:
        r = self._reservations.pop(job_id, None)
        if r is not None:
            nlist = self._node_index.get((r.tenant_id, r.node_id), [])
            if job_id in nlist:
                nlist.remove(job_id)
        return r

    def get(self, job_id: str) -> ResourceReservation | None:
        return self._reservations.get(job_id)

    def get_by_node(
        self,
        node_id: str,
        *,
        tenant_id: str = "default",
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        jids = self._node_index.get((tenant_id, node_id), [])
        result = [self._reservations[jid] for jid in jids if jid in self._reservations]
        if after is not None:
            result = [r for r in result if r.end_at > after]
        return sorted(result, key=lambda r: r.start_at)

    def list(
        self,
        *,
        tenant_id: str | None = None,
        node_id: str | None = None,
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        result = list(self._reservations.values())
        if tenant_id is not None:
            result = [r for r in result if r.tenant_id == tenant_id]
        if node_id is not None:
            result = [r for r in result if r.node_id == node_id]
        if after is not None:
            result = [r for r in result if r.end_at > after]
        return sorted(result, key=lambda r: (r.start_at, r.node_id, r.job_id))

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
    - ``zen70:reservations:data:{job_id}`` 鈥?JSON hash of reservation
    - ``zen70:reservations:node:{node_id}`` 鈥?sorted set (score = start_at epoch)
    - ``zen70:reservations:count`` 鈥?atomic counter (approximation)
    """

    _PREFIX = "zen70:reservations"

    def __init__(self, redis_client: SyncRedisClient, max_reservations: int = 50) -> None:
        self._redis = redis_client
        self._max = max_reservations

    def _data_key(self, job_id: str) -> str:
        return f"{self._PREFIX}:data:{job_id}"

    def _node_key(self, tenant_id: str, node_id: str) -> str:
        return f"{self._PREFIX}:tenant:{tenant_id}:node:{node_id}"

    def _count_key(self) -> str:
        return f"{self._PREFIX}:count"

    def put(self, reservation: ResourceReservation) -> bool:
        r = self._redis
        # Check current count (approximate 鈥?race is acceptable for capacity limit)
        current = int(r.kv.get(self._count_key()) or 0)
        # Idempotent: if already exists, just return True
        if r.kv.exists(self._data_key(reservation.job_id)):
            return True
        if current >= self._max:
            return False

        r.kv.set(
            self._data_key(reservation.job_id),
            json.dumps(reservation.to_dict()),
            ex=max(int((reservation.end_at - datetime.datetime.now(datetime.UTC)).total_seconds()), 60),
        )
        r.sorted_sets.add(
            self._node_key(reservation.tenant_id, reservation.node_id),
            {reservation.job_id: reservation.start_at.timestamp()},
        )
        r.kv.incr(self._count_key())
        return True

    def remove(self, job_id: str) -> ResourceReservation | None:
        r = self._redis
        raw = r.kv.get(self._data_key(job_id))
        if raw is None:
            return None
        data = json.loads(raw)
        reservation = ResourceReservation.from_dict(data)
        r.kv.delete(self._data_key(job_id))
        r.sorted_sets.remove(self._node_key(reservation.tenant_id, reservation.node_id), job_id)
        if r.kv.decr(self._count_key()) < 0:
            r.kv.set(self._count_key(), 0)
        return reservation

    def get(self, job_id: str) -> ResourceReservation | None:
        r = self._redis
        raw = r.kv.get(self._data_key(job_id))
        if raw is None:
            return None
        return ResourceReservation.from_dict(json.loads(raw))

    def get_by_node(
        self,
        node_id: str,
        *,
        tenant_id: str = "default",
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        r = self._redis
        min_score = after.timestamp() if after else "-inf"
        jids = r.sorted_sets.range_by_score(self._node_key(tenant_id, node_id), min_score, "+inf")
        result: list[ResourceReservation] = []
        for jid in jids:
            raw = r.kv.get(self._data_key(jid))
            if raw is None:
                r.sorted_sets.remove(self._node_key(tenant_id, node_id), jid)
                continue
            reservation = ResourceReservation.from_dict(json.loads(raw))
            if after is None or reservation.end_at > after:
                result.append(reservation)
        return sorted(result, key=lambda rv: rv.start_at)

    def list(
        self,
        *,
        tenant_id: str | None = None,
        node_id: str | None = None,
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        if tenant_id is not None and node_id is not None:
            return self.get_by_node(node_id, tenant_id=tenant_id, after=after)

        result: list[ResourceReservation] = []
        for key in self._redis.kv.scan_prefix(f"{self._PREFIX}:data:"):
            job_id = key.rsplit(":", 1)[-1]
            reservation = self.get(job_id)
            if reservation is None:
                continue
            if tenant_id is not None and reservation.tenant_id != tenant_id:
                continue
            if node_id is not None and reservation.node_id != node_id:
                continue
            if after is not None and reservation.end_at <= after:
                continue
            result.append(reservation)
        return sorted(result, key=lambda rv: (rv.start_at, rv.node_id, rv.job_id))

    def count(self) -> int:
        r = self._redis
        return max(int(r.kv.get(self._count_key()) or 0), 0)

    def cleanup_expired(self, now: datetime.datetime) -> int:
        # Redis TTL on data keys handles most cleanup automatically.
        # This method handles node index housekeeping.
        r = self._redis
        removed = 0
        # Scan node keys and remove entries whose data has expired
        node_prefix = f"{self._PREFIX}:tenant:"
        for nkey in r.kv.scan_prefix(node_prefix):
            members = r.sorted_sets.range_by_score(nkey, "-inf", "+inf")
            for jid in members:
                if not r.kv.exists(self._data_key(jid)):
                    r.sorted_sets.remove(nkey, jid)
                    if r.kv.decr(self._count_key()) < 0:
                        r.kv.set(self._count_key(), 0)
                    removed += 1
        return removed


# =====================================================================
# Reservation Manager 鈥?maintains the reservation table
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
            tenant_id=_resolve_tenant_id(getattr(job, "tenant_id", "default")),
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
        return self._store.list(tenant_id=tenant_id, node_id=node_id, after=after)

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
        return "redis" if isinstance(self._store, RedisReservationStore) else "memory"

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
        resolved_tenant_id = tenant_id or _resolve_tenant_id(getattr(node, "tenant_id", "default"))
        reservations = self.get_node_reservations(node.node_id, tenant_id=resolved_tenant_id, after=now)

        if not reservations:
            # No reservations 鈥?entire horizon is open
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
# Backfill Evaluator 鈥?determines which jobs can backfill
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
        tenant_id = _resolve_tenant_id(getattr(job, "tenant_id", None))
        if tenant_id == "default":
            tenant_id = _resolve_tenant_id(getattr(node, "tenant_id", "default"))
        reservations = self._reservation_mgr.get_node_reservations(node.node_id, tenant_id=tenant_id, after=now)

        for r in reservations:
            if r.job_id == job.job_id:
                continue  # This job's own reservation
            if estimated_end > r.start_at:
                return False, f"would_delay_reservation:{r.job_id}:starts_at:{r.start_at.isoformat()}"

        return True, "ok"


# =====================================================================
# Scheduling Constraints 鈥?integrate with the constraint pipeline
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
            return True, ""  # High priority 鈥?not a backfill candidate

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

        # Select store backend from compiled runtime env.
        store = _build_reservation_store(config)

        _reservation_manager = ReservationManager(config, store=store)
    return _reservation_manager


def reset_reservation_manager() -> None:
    """Reset singleton (for tests)."""
    global _reservation_manager
    _reservation_manager = None
