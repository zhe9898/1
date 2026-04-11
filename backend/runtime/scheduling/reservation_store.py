from __future__ import annotations

import datetime
import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from backend.platform.redis import SyncRedisClient
from backend.runtime.scheduling.reservation_models import ResourceReservation


@dataclass(frozen=True, slots=True)
class ReservationQuery:
    tenant_id: str | None = None
    node_id: str | None = None
    after: datetime.datetime | None = None

    @classmethod
    def for_node(
        cls,
        node_id: str,
        *,
        tenant_id: str = "default",
        after: datetime.datetime | None = None,
    ) -> ReservationQuery:
        return cls(tenant_id=tenant_id, node_id=node_id, after=after)

    def matches(self, reservation: ResourceReservation) -> bool:
        if self.tenant_id is not None and reservation.tenant_id != self.tenant_id:
            return False
        if self.node_id is not None and reservation.node_id != self.node_id:
            return False
        if self.after is not None and reservation.end_at <= self.after:
            return False
        return True


def _sort_reservations(reservations: Iterable[ResourceReservation]) -> list[ResourceReservation]:
    return sorted(reservations, key=lambda reservation: (reservation.start_at, reservation.node_id, reservation.job_id))


def _sort_node_reservations(reservations: Iterable[ResourceReservation]) -> list[ResourceReservation]:
    return sorted(reservations, key=lambda reservation: reservation.start_at)


class ReservationStore(ABC):
    """Abstract interface for reservation persistence."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        ...

    @abstractmethod
    def put(self, reservation: ResourceReservation) -> bool:
        ...

    @abstractmethod
    def remove(self, job_id: str) -> ResourceReservation | None:
        ...

    @abstractmethod
    def get(self, job_id: str) -> ResourceReservation | None:
        ...

    @abstractmethod
    def get_by_node(
        self,
        node_id: str,
        *,
        tenant_id: str = "default",
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        ...

    @abstractmethod
    def list(self, query: ReservationQuery | None = None) -> list[ResourceReservation]:
        ...

    @abstractmethod
    def count(self) -> int:
        ...

    @abstractmethod
    def cleanup_expired(self, now: datetime.datetime) -> int:
        ...


class InMemoryReservationStore(ReservationStore):
    """Process-local dict-based store (single-gateway deployments)."""

    def __init__(self, max_reservations: int = 50) -> None:
        self._max = max_reservations
        self._reservations: dict[str, ResourceReservation] = {}
        self._node_index: dict[tuple[str, str], list[str]] = {}

    @property
    def backend_name(self) -> str:
        return "memory"

    def put(self, reservation: ResourceReservation) -> bool:
        if reservation.job_id in self._reservations:
            return True
        if len(self._reservations) >= self._max:
            return False
        self._reservations[reservation.job_id] = reservation
        self._node_index.setdefault((reservation.tenant_id, reservation.node_id), []).append(reservation.job_id)
        return True

    def remove(self, job_id: str) -> ResourceReservation | None:
        reservation = self._reservations.pop(job_id, None)
        if reservation is None:
            return None
        node_reservations = self._node_index.get((reservation.tenant_id, reservation.node_id), [])
        if job_id in node_reservations:
            node_reservations.remove(job_id)
        return reservation

    def get(self, job_id: str) -> ResourceReservation | None:
        return self._reservations.get(job_id)

    def _reservations_for_job_ids(self, job_ids: Sequence[str]) -> list[ResourceReservation]:
        return [self._reservations[job_id] for job_id in job_ids if job_id in self._reservations]

    def get_by_node(
        self,
        node_id: str,
        *,
        tenant_id: str = "default",
        after: datetime.datetime | None = None,
    ) -> list[ResourceReservation]:
        query = ReservationQuery.for_node(node_id, tenant_id=tenant_id, after=after)
        job_ids = self._node_index.get((tenant_id, node_id), [])
        return _sort_node_reservations(
            reservation
            for reservation in self._reservations_for_job_ids(job_ids)
            if query.matches(reservation)
        )

    def list(self, query: ReservationQuery | None = None) -> list[ResourceReservation]:
        resolved_query = query or ReservationQuery()
        if resolved_query.tenant_id is not None and resolved_query.node_id is not None:
            return self.get_by_node(
                resolved_query.node_id,
                tenant_id=resolved_query.tenant_id,
                after=resolved_query.after,
            )
        return _sort_reservations(
            reservation
            for reservation in self._reservations.values()
            if resolved_query.matches(reservation)
        )

    def count(self) -> int:
        return len(self._reservations)

    def cleanup_expired(self, now: datetime.datetime) -> int:
        expired = [job_id for job_id, reservation in self._reservations.items() if reservation.is_expired(now)]
        for job_id in expired:
            self.remove(job_id)
        return len(expired)


class RedisReservationStore(ReservationStore):
    """Redis-backed distributed store for multi-gateway deployments."""

    _PREFIX = "zen70:reservations"

    def __init__(self, redis_client: SyncRedisClient, max_reservations: int = 50) -> None:
        self._redis = redis_client
        self._max = max_reservations

    @property
    def backend_name(self) -> str:
        return "redis"

    def _data_key(self, job_id: str) -> str:
        return f"{self._PREFIX}:data:{job_id}"

    def _data_prefix(self) -> str:
        return f"{self._PREFIX}:data:"

    def _node_key(self, tenant_id: str, node_id: str) -> str:
        return f"{self._PREFIX}:tenant:{tenant_id}:node:{node_id}"

    def _node_prefix(self, tenant_id: str | None = None) -> str:
        if tenant_id is None:
            return f"{self._PREFIX}:tenant:"
        return f"{self._PREFIX}:tenant:{tenant_id}:node:"

    def _count_key(self) -> str:
        return f"{self._PREFIX}:count"

    def _job_id_from_data_key(self, key: str) -> str:
        return key.rsplit(":", 1)[-1]

    def _remove_stale_node_members(self, index_references: dict[str, set[str]], job_id: str) -> None:
        for node_key in index_references.get(job_id, set()):
            self._redis.sorted_sets.remove(node_key, job_id)

    def _load_reservations(
        self,
        job_ids: Sequence[str],
        *,
        index_references: dict[str, set[str]] | None = None,
    ) -> list[ResourceReservation]:
        if not job_ids:
            return []
        raw_records = self._redis.kv.get_many([self._data_key(job_id) for job_id in job_ids])
        reservations: list[ResourceReservation] = []
        for job_id, raw in zip(job_ids, raw_records, strict=False):
            if raw is None:
                if index_references is not None:
                    self._remove_stale_node_members(index_references, job_id)
                continue
            reservations.append(ResourceReservation.from_dict(json.loads(raw)))
        return reservations

    def _scan_node_keys(self, query: ReservationQuery) -> list[str]:
        if query.tenant_id is not None and query.node_id is not None:
            return [self._node_key(query.tenant_id, query.node_id)]
        if query.tenant_id is not None:
            return self._redis.kv.scan_prefix(self._node_prefix(query.tenant_id))
        node_keys = self._redis.kv.scan_prefix(self._node_prefix())
        if query.node_id is None:
            return node_keys
        suffix = f":node:{query.node_id}"
        return [node_key for node_key in node_keys if node_key.endswith(suffix)]

    def _collect_job_ids_from_node_indexes(
        self,
        query: ReservationQuery,
    ) -> tuple[list[str], dict[str, set[str]]]:
        job_ids: list[str] = []
        index_references: dict[str, set[str]] = {}
        for node_key in self._scan_node_keys(query):
            # The node index is scored by reservation start time, but query.after
            # means reservation.end_at > after, so we must hydrate and filter.
            for job_id in self._redis.sorted_sets.range_by_score(node_key, "-inf", "+inf"):
                job_ids.append(job_id)
                index_references.setdefault(job_id, set()).add(node_key)
        return list(dict.fromkeys(job_ids)), index_references

    def put(self, reservation: ResourceReservation) -> bool:
        current = int(self._redis.kv.get(self._count_key()) or 0)
        if self._redis.kv.exists(self._data_key(reservation.job_id)):
            return True
        if current >= self._max:
            return False

        self._redis.kv.set(
            self._data_key(reservation.job_id),
            json.dumps(reservation.to_dict()),
            ex=max(int((reservation.end_at - datetime.datetime.now(datetime.UTC)).total_seconds()), 60),
        )
        self._redis.sorted_sets.add(
            self._node_key(reservation.tenant_id, reservation.node_id),
            {reservation.job_id: reservation.start_at.timestamp()},
        )
        self._redis.kv.incr(self._count_key())
        return True

    def remove(self, job_id: str) -> ResourceReservation | None:
        raw = self._redis.kv.get(self._data_key(job_id))
        if raw is None:
            return None
        reservation = ResourceReservation.from_dict(json.loads(raw))
        self._redis.kv.delete(self._data_key(job_id))
        self._redis.sorted_sets.remove(self._node_key(reservation.tenant_id, reservation.node_id), job_id)
        if self._redis.kv.decr(self._count_key()) < 0:
            self._redis.kv.set(self._count_key(), 0)
        return reservation

    def get(self, job_id: str) -> ResourceReservation | None:
        raw = self._redis.kv.get(self._data_key(job_id))
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
        query = ReservationQuery.for_node(node_id, tenant_id=tenant_id, after=after)
        job_ids, index_references = self._collect_job_ids_from_node_indexes(query)
        return _sort_node_reservations(
            reservation
            for reservation in self._load_reservations(job_ids, index_references=index_references)
            if query.matches(reservation)
        )

    def list(self, query: ReservationQuery | None = None) -> list[ResourceReservation]:
        resolved_query = query or ReservationQuery()
        if resolved_query.tenant_id is not None and resolved_query.node_id is not None:
            return self.get_by_node(
                resolved_query.node_id,
                tenant_id=resolved_query.tenant_id,
                after=resolved_query.after,
            )
        if resolved_query.tenant_id is None and resolved_query.node_id is None:
            job_ids = [
                self._job_id_from_data_key(key)
                for key in self._redis.kv.scan_prefix(self._data_prefix())
            ]
            reservations = self._load_reservations(job_ids)
        else:
            job_ids, index_references = self._collect_job_ids_from_node_indexes(resolved_query)
            reservations = self._load_reservations(job_ids, index_references=index_references)
        return _sort_reservations(
            reservation
            for reservation in reservations
            if resolved_query.matches(reservation)
        )

    def count(self) -> int:
        return max(int(self._redis.kv.get(self._count_key()) or 0), 0)

    def cleanup_expired(self, now: datetime.datetime) -> int:
        removed = 0
        for node_key in self._redis.kv.scan_prefix(self._node_prefix()):
            members = self._redis.sorted_sets.range_by_score(node_key, "-inf", "+inf")
            for job_id in members:
                if not self._redis.kv.exists(self._data_key(job_id)):
                    self._redis.sorted_sets.remove(node_key, job_id)
                    if self._redis.kv.decr(self._count_key()) < 0:
                        self._redis.kv.set(self._count_key(), 0)
                    removed += 1
        return removed


__all__ = (
    "InMemoryReservationStore",
    "RedisReservationStore",
    "ReservationQuery",
    "ReservationStore",
)
