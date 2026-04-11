"""Shared support for backfill scheduling tests."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from backend.runtime.scheduling.backfill_scheduling import (
    BackfillConfig,
    BackfillEvaluator,
    BackfillGate,
    ReservationHonorGate,
    ReservationManager,
    get_reservation_manager,
    reset_reservation_manager,
)
from backend.runtime.scheduling.reservation_models import ResourceReservation
from backend.runtime.scheduling.reservation_store import InMemoryReservationStore, RedisReservationStore, ReservationQuery
from backend.runtime.scheduling.scheduling_constraints import SchedulingContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _make_job(**overrides) -> MagicMock:
    job = MagicMock()
    job.job_id = overrides.get("job_id", "job-1")
    job.priority = overrides.get("priority", 50)
    job.created_at = overrides.get("created_at", _utcnow())
    job.tenant_id = overrides.get("tenant_id", "default")
    job.required_cpu_cores = overrides.get("required_cpu_cores", 4)
    job.required_memory_mb = overrides.get("required_memory_mb", 8192)
    job.required_gpu_vram_mb = overrides.get("required_gpu_vram_mb", 0)
    job.estimated_duration_s = overrides.get("estimated_duration_s", 300)
    job.status = overrides.get("status", "pending")
    return job


def _make_node(**overrides) -> MagicMock:
    node = MagicMock()
    node.node_id = overrides.get("node_id", "node-1")
    node.max_concurrency = overrides.get("max_concurrency", 4)
    node.active_lease_count = overrides.get("active_lease_count", 0)
    return node


def _make_ctx(now: datetime.datetime | None = None) -> SchedulingContext:
    return SchedulingContext(
        now=now or _utcnow(),
        completed_job_ids=set(),
        available_slots=4,
        parent_jobs={},
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset reservation manager singleton between tests."""
    reset_reservation_manager()
    yield
    reset_reservation_manager()


class _FakeRedisKV:
    def __init__(self, data: dict[str, str], *, scan_keys: list[str]) -> None:
        self._data = data
        self._scan_keys = scan_keys
        self.scan_prefix_calls: list[str] = []

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def get_many(self, keys: list[str], *, transactional: bool = True) -> list[str | None]:
        return [self._data.get(key) for key in keys]

    def exists(self, key: str) -> bool:
        return key in self._data

    def scan_prefix(self, prefix: str, *, count: int = 100) -> list[str]:
        self.scan_prefix_calls.append(prefix)
        return [key for key in self._scan_keys if key.startswith(prefix)]

    def set(self, key: str, value: str | int, **_: object) -> bool:
        self._data[key] = str(value)
        return True

    def incr(self, key: str) -> int:
        value = int(self._data.get(key, "0")) + 1
        self._data[key] = str(value)
        return value

    def decr(self, key: str) -> int:
        value = int(self._data.get(key, "0")) - 1
        self._data[key] = str(value)
        return value

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._data:
                removed += 1
                del self._data[key]
        return removed


class _FakeRedisSortedSets:
    def __init__(self, members: dict[str, list[str]]) -> None:
        self._members = {key: list(value) for key, value in members.items()}
        self.remove_calls: list[tuple[str, tuple[str, ...]]] = []

    def range_by_score(self, key: str, min_score: float | str, max_score: float | str) -> list[str]:
        return list(self._members.get(key, []))

    def remove(self, key: str, *members: str) -> int:
        self.remove_calls.append((key, members))
        existing = self._members.get(key, [])
        self._members[key] = [member for member in existing if member not in set(members)]
        return 1

    def add(self, key: str, mapping: dict[str, float]) -> int:
        self._members.setdefault(key, []).extend(mapping)
        return len(mapping)


class _FakeRedisClient:
    def __init__(self, data: dict[str, str], *, sorted_sets: dict[str, list[str]], scan_keys: list[str]) -> None:
        self.kv = _FakeRedisKV(data, scan_keys=scan_keys)
        self.sorted_sets = _FakeRedisSortedSets(sorted_sets)


__all__ = [
    "BackfillConfig",
    "BackfillEvaluator",
    "BackfillGate",
    "InMemoryReservationStore",
    "RedisReservationStore",
    "ReservationHonorGate",
    "ReservationManager",
    "ReservationQuery",
    "ResourceReservation",
    "SchedulingContext",
    "_FakeRedisClient",
    "_make_ctx",
    "_make_job",
    "_make_node",
    "_utcnow",
    "get_reservation_manager",
    "reset_reservation_manager",
]
