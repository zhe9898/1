from __future__ import annotations

import datetime
import json

from .backfill_scheduling_test_support import (
    InMemoryReservationStore,
    RedisReservationStore,
    ReservationManager,
    ReservationQuery,
    ResourceReservation,
    _FakeRedisClient,
    _make_job,
    _make_node,
    _reset_singleton,
    _utcnow,
)

__fixtures__ = (_reset_singleton,)

# =====================================================================
# InMemoryReservationStore
# =====================================================================


class TestInMemoryReservationStore:
    """Verify the InMemoryReservationStore independently of ReservationManager."""

    def test_put_and_get(self):
        store = InMemoryReservationStore(max_reservations=10)
        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=80,
            cpu_cores=2.0,
        )
        assert store.put(r) is True
        assert store.get("j1") is r
        assert store.count() == 1

    def test_idempotent_put(self):
        store = InMemoryReservationStore(max_reservations=10)
        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=80,
        )
        store.put(r)
        assert store.put(r) is True  # idempotent
        assert store.count() == 1

    def test_capacity_limit(self):
        store = InMemoryReservationStore(max_reservations=2)
        now = _utcnow()
        for i in range(2):
            r = ResourceReservation(
                job_id=f"j{i}",
                node_id="n1",
                start_at=now,
                end_at=now + datetime.timedelta(seconds=300),
                priority=80,
            )
            assert store.put(r) is True
        # Third should fail
        r3 = ResourceReservation(
            job_id="j2",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=80,
        )
        assert store.put(r3) is False
        assert store.count() == 2

    def test_remove(self):
        store = InMemoryReservationStore()
        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=80,
        )
        store.put(r)
        removed = store.remove("j1")
        assert removed is r
        assert store.get("j1") is None
        assert store.count() == 0

    def test_remove_nonexistent(self):
        store = InMemoryReservationStore()
        assert store.remove("nonexistent") is None

    def test_get_by_node(self):
        store = InMemoryReservationStore()
        now = _utcnow()
        r1 = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=80,
        )
        r2 = ResourceReservation(
            job_id="j2",
            node_id="n1",
            start_at=now + datetime.timedelta(seconds=400),
            end_at=now + datetime.timedelta(seconds=700),
            priority=70,
        )
        r3 = ResourceReservation(
            job_id="j3",
            node_id="n2",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=60,
        )
        store.put(r1)
        store.put(r2)
        store.put(r3)

        n1_reservations = store.get_by_node("n1")
        assert len(n1_reservations) == 2
        assert n1_reservations[0].job_id == "j1"  # sorted by start_at
        assert n1_reservations[1].job_id == "j2"

    def test_get_by_node_filtered_by_time(self):
        store = InMemoryReservationStore()
        now = _utcnow()
        r_past = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now - datetime.timedelta(seconds=600),
            end_at=now - datetime.timedelta(seconds=1),
            priority=80,
        )
        r_future = ResourceReservation(
            job_id="j2",
            node_id="n1",
            start_at=now + datetime.timedelta(seconds=100),
            end_at=now + datetime.timedelta(seconds=400),
            priority=70,
        )
        store.put(r_past)
        store.put(r_future)

        # Filter: after=now → only future reservation
        result = store.get_by_node("n1", after=now)
        assert len(result) == 1
        assert result[0].job_id == "j2"

    def test_cleanup_expired(self):
        store = InMemoryReservationStore()
        now = _utcnow()
        r_expired = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now - datetime.timedelta(seconds=600),
            end_at=now - datetime.timedelta(seconds=1),
            priority=80,
        )
        r_active = ResourceReservation(
            job_id="j2",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=70,
        )
        store.put(r_expired)
        store.put(r_active)

        removed = store.cleanup_expired(now)
        assert removed == 1
        assert store.count() == 1
        assert store.get("j1") is None
        assert store.get("j2") is not None

    def test_list_query_filters_without_leaking_store_specific_args(self):
        store = InMemoryReservationStore()
        now = _utcnow()
        store.put(
            ResourceReservation(
                job_id="j1",
                tenant_id="tenant-a",
                node_id="n1",
                start_at=now - datetime.timedelta(seconds=300),
                end_at=now + datetime.timedelta(seconds=60),
                priority=90,
            )
        )
        store.put(
            ResourceReservation(
                job_id="j2",
                tenant_id="tenant-a",
                node_id="n2",
                start_at=now + datetime.timedelta(seconds=120),
                end_at=now + datetime.timedelta(seconds=420),
                priority=80,
            )
        )
        store.put(
            ResourceReservation(
                job_id="j3",
                tenant_id="tenant-b",
                node_id="n1",
                start_at=now,
                end_at=now + datetime.timedelta(seconds=300),
                priority=70,
            )
        )

        result = store.list(ReservationQuery(tenant_id="tenant-a", after=now))

        assert [reservation.job_id for reservation in result] == ["j1", "j2"]


class TestRedisReservationStore:
    def test_list_scopes_to_tenant_node_indexes_and_keeps_overlapping_reservations(self):
        now = _utcnow()
        prefix = "zen70:reservations"
        node_key_a = f"{prefix}:tenant:tenant-a:node:n1"
        node_key_b = f"{prefix}:tenant:tenant-b:node:n1"
        reservations = {
            f"{prefix}:data:j1": json.dumps(
                ResourceReservation(
                    job_id="j1",
                    tenant_id="tenant-a",
                    node_id="n1",
                    start_at=now - datetime.timedelta(seconds=300),
                    end_at=now + datetime.timedelta(seconds=60),
                    priority=90,
                ).to_dict()
            ),
            f"{prefix}:data:j2": json.dumps(
                ResourceReservation(
                    job_id="j2",
                    tenant_id="tenant-a",
                    node_id="n1",
                    start_at=now + datetime.timedelta(seconds=120),
                    end_at=now + datetime.timedelta(seconds=420),
                    priority=80,
                ).to_dict()
            ),
            f"{prefix}:data:j3": json.dumps(
                ResourceReservation(
                    job_id="j3",
                    tenant_id="tenant-b",
                    node_id="n1",
                    start_at=now,
                    end_at=now + datetime.timedelta(seconds=300),
                    priority=70,
                ).to_dict()
            ),
        }
        fake_redis = _FakeRedisClient(
            reservations,
            sorted_sets={node_key_a: ["j1", "j2"], node_key_b: ["j3"]},
            scan_keys=[node_key_a, node_key_b, *reservations.keys()],
        )
        store = RedisReservationStore(fake_redis)

        result = store.list(ReservationQuery(tenant_id="tenant-a", after=now))

        assert [reservation.job_id for reservation in result] == ["j1", "j2"]
        assert fake_redis.kv.scan_prefix_calls == [f"{prefix}:tenant:tenant-a:node:"]

    def test_get_by_node_prunes_stale_index_members(self):
        now = _utcnow()
        prefix = "zen70:reservations"
        node_key = f"{prefix}:tenant:tenant-a:node:n1"
        reservations = {
            f"{prefix}:data:j1": json.dumps(
                ResourceReservation(
                    job_id="j1",
                    tenant_id="tenant-a",
                    node_id="n1",
                    start_at=now,
                    end_at=now + datetime.timedelta(seconds=300),
                    priority=90,
                ).to_dict()
            )
        }
        fake_redis = _FakeRedisClient(
            reservations,
            sorted_sets={node_key: ["j-missing", "j1"]},
            scan_keys=[node_key, *reservations.keys()],
        )
        store = RedisReservationStore(fake_redis)

        result = store.get_by_node("n1", tenant_id="tenant-a", after=now)

        assert [reservation.job_id for reservation in result] == ["j1"]
        assert fake_redis.sorted_sets.remove_calls == [(node_key, ("j-missing",))]


# =====================================================================
# ResourceReservation serialization
# =====================================================================


class TestReservationSerialization:
    def test_round_trip(self):
        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=600),
            priority=80,
            cpu_cores=4.0,
            memory_mb=8192.0,
            gpu_vram_mb=1024.0,
            slots=2,
        )
        data = r.to_dict()
        r2 = ResourceReservation.from_dict(data)
        assert r2.job_id == r.job_id
        assert r2.node_id == r.node_id
        assert r2.start_at == r.start_at
        assert r2.end_at == r.end_at
        assert r2.priority == r.priority
        assert r2.cpu_cores == r.cpu_cores
        assert r2.memory_mb == r.memory_mb
        assert r2.gpu_vram_mb == r.gpu_vram_mb
        assert r2.slots == r.slots

    def test_to_dict_is_json_serializable(self):
        import json

        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=300),
            priority=50,
        )
        # Should not raise
        serialized = json.dumps(r.to_dict())
        assert isinstance(serialized, str)


# =====================================================================
# ReservationManager with explicit store injection
# =====================================================================


class TestReservationManagerWithStore:
    def test_uses_injected_store(self):
        """ReservationManager delegates to the injected store."""
        store = InMemoryReservationStore(max_reservations=5)
        mgr = ReservationManager(store=store)
        job = _make_job(job_id="j1", priority=80)
        node = _make_node(node_id="n1")
        now = _utcnow()

        r = mgr.create_reservation(job, node, start_at=now)
        assert r is not None
        # Verify the store holds the reservation
        assert store.get("j1") is not None
        assert store.count() == 1

    def test_cancel_delegates_to_store(self):
        store = InMemoryReservationStore()
        mgr = ReservationManager(store=store)
        job = _make_job(job_id="j1")
        node = _make_node()
        mgr.create_reservation(job, node, start_at=_utcnow())

        assert mgr.cancel_reservation("j1") is True
        assert store.get("j1") is None

    def test_cleanup_delegates_to_store(self):
        store = InMemoryReservationStore()
        mgr = ReservationManager(store=store)
        now = _utcnow()
        node = _make_node()

        mgr.create_reservation(
            _make_job(job_id="j1"),
            node,
            start_at=now - datetime.timedelta(seconds=600),
            estimated_duration_s=100,
        )
        mgr.create_reservation(
            _make_job(job_id="j2"),
            node,
            start_at=now + datetime.timedelta(seconds=100),
            estimated_duration_s=300,
        )

        removed = mgr.cleanup_expired(now)
        assert removed == 1
        assert mgr.reservation_count == 1
