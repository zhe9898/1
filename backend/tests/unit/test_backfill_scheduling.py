"""Tests for backfill & reservation scheduling module.

Covers:
- BackfillConfig defaults and immutability
- ReservationManager CRUD (create, cancel, cleanup, find windows)
- BackfillEvaluator (can_backfill logic)
- ReservationHonorGate (priority boost near reservation windows)
- BackfillGate (marks low-priority as backfill candidates)
- Policy store integration (reads BackfillPolicyConfig)
- Singleton lifecycle (get/reset)
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from backend.core.backfill_scheduling import (
    BackfillConfig,
    BackfillEvaluator,
    BackfillGate,
    ReservationHonorGate,
    ReservationManager,
    ResourceReservation,
    get_reservation_manager,
    reset_reservation_manager,
)
from backend.core.scheduling_constraints import SchedulingContext

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


# =====================================================================
# BackfillConfig defaults
# =====================================================================


class TestBackfillConfigDefaults:
    def test_defaults(self):
        cfg = BackfillConfig()
        assert cfg.enabled is True
        assert cfg.max_reservations == 50
        assert cfg.default_estimated_duration_s == 300
        assert cfg.max_backfill_duration_s == 0
        assert cfg.planning_horizon_s == 3600
        assert cfg.min_gap_s == 30
        assert cfg.reservation_min_priority == 70

    def test_frozen(self):
        cfg = BackfillConfig()
        with pytest.raises(AttributeError):
            cfg.enabled = False  # type: ignore[misc]


# =====================================================================
# ResourceReservation
# =====================================================================


class TestResourceReservation:
    def test_overlaps_true(self):
        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=600),
            priority=80,
        )
        # Overlap: starts during reservation
        assert r.overlaps(now + datetime.timedelta(seconds=100), now + datetime.timedelta(seconds=700))

    def test_overlaps_false(self):
        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now,
            end_at=now + datetime.timedelta(seconds=600),
            priority=80,
        )
        # No overlap: entirely after
        assert not r.overlaps(now + datetime.timedelta(seconds=600), now + datetime.timedelta(seconds=900))

    def test_is_expired(self):
        now = _utcnow()
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=now - datetime.timedelta(seconds=600),
            end_at=now - datetime.timedelta(seconds=1),
            priority=80,
        )
        assert r.is_expired(now)

    def test_resource_conflicts(self):
        r = ResourceReservation(
            job_id="j1",
            node_id="n1",
            start_at=_utcnow(),
            end_at=_utcnow() + datetime.timedelta(seconds=600),
            priority=80,
            cpu_cores=4.0,
        )
        assert r.resource_conflicts(cpu=2.0)
        assert not r.resource_conflicts(memory=1024.0)


# =====================================================================
# ReservationManager
# =====================================================================


class TestReservationManager:
    def test_create_and_get(self):
        mgr = ReservationManager()
        job = _make_job(job_id="j1", priority=80)
        node = _make_node(node_id="n1")
        now = _utcnow()

        r = mgr.create_reservation(job, node, start_at=now)
        assert r is not None
        assert r.job_id == "j1"
        assert r.node_id == "n1"
        assert mgr.reservation_count == 1
        assert mgr.get_reservation("j1") is r

    def test_duplicate_returns_existing(self):
        mgr = ReservationManager()
        job = _make_job(job_id="j1", priority=80)
        node = _make_node()
        now = _utcnow()

        r1 = mgr.create_reservation(job, node, start_at=now)
        r2 = mgr.create_reservation(job, node, start_at=now)
        assert r1 is r2
        assert mgr.reservation_count == 1

    def test_max_reservations(self):
        cfg = BackfillConfig(max_reservations=2)
        mgr = ReservationManager(config=cfg)
        node = _make_node()
        now = _utcnow()

        mgr.create_reservation(_make_job(job_id="j1"), node, start_at=now)
        mgr.create_reservation(_make_job(job_id="j2"), node, start_at=now)
        r3 = mgr.create_reservation(_make_job(job_id="j3"), node, start_at=now)
        assert r3 is None
        assert mgr.reservation_count == 2

    def test_cancel(self):
        mgr = ReservationManager()
        job = _make_job(job_id="j1")
        node = _make_node()
        mgr.create_reservation(job, node, start_at=_utcnow())

        assert mgr.cancel_reservation("j1") is True
        assert mgr.reservation_count == 0
        assert mgr.cancel_reservation("j1") is False  # Already cancelled

    def test_get_node_reservations(self):
        mgr = ReservationManager()
        now = _utcnow()
        node = _make_node(node_id="n1")

        mgr.create_reservation(
            _make_job(job_id="j1"),
            node,
            start_at=now,
            estimated_duration_s=300,
        )
        mgr.create_reservation(
            _make_job(job_id="j2"),
            node,
            start_at=now + datetime.timedelta(seconds=400),
            estimated_duration_s=200,
        )

        reservations = mgr.get_node_reservations("n1")
        assert len(reservations) == 2
        assert reservations[0].job_id == "j1"  # sorted by start_at

    def test_cleanup_expired(self):
        mgr = ReservationManager()
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

    def test_find_backfill_window_no_reservations(self):
        mgr = ReservationManager()
        node = _make_node()
        now = _utcnow()

        window = mgr.find_backfill_window(node, now=now, required_duration_s=60)
        assert window is not None
        assert window[0] == now

    def test_find_backfill_window_before_first_reservation(self):
        mgr = ReservationManager()
        now = _utcnow()
        node = _make_node()

        mgr.create_reservation(
            _make_job(job_id="j1"),
            node,
            start_at=now + datetime.timedelta(seconds=120),
            estimated_duration_s=300,
        )

        # Should find gap before first reservation
        window = mgr.find_backfill_window(node, now=now, required_duration_s=60)
        assert window is not None
        assert window[0] == now
        assert window[1] == now + datetime.timedelta(seconds=120)

    def test_find_backfill_window_too_small(self):
        cfg = BackfillConfig(min_gap_s=120, planning_horizon_s=60)
        mgr = ReservationManager(config=cfg)
        now = _utcnow()
        node = _make_node()

        # Reservation starts in 30s and runs for 3000s (past the 60s horizon)
        mgr.create_reservation(
            _make_job(job_id="j1"),
            node,
            start_at=now + datetime.timedelta(seconds=30),
            estimated_duration_s=3000,
        )

        # Gap before reservation is only 30s, but min_gap is 120s
        # Gap after reservation is past the planning horizon
        window = mgr.find_backfill_window(node, now=now, required_duration_s=20)
        assert window is None


# =====================================================================
# BackfillEvaluator
# =====================================================================


class TestBackfillEvaluator:
    def test_can_backfill_no_reservations(self):
        mgr = ReservationManager()
        evaluator = BackfillEvaluator(mgr)
        job = _make_job(estimated_duration_s=60)
        node = _make_node()

        can, reason = evaluator.can_backfill(job, node, now=_utcnow())
        assert can is True

    def test_cannot_backfill_would_delay_reservation(self):
        mgr = ReservationManager()
        evaluator = BackfillEvaluator(mgr)
        now = _utcnow()
        node = _make_node()

        mgr.create_reservation(
            _make_job(job_id="reserved-1", priority=90),
            node,
            start_at=now + datetime.timedelta(seconds=60),
            estimated_duration_s=300,
        )

        job = _make_job(job_id="backfill-1", estimated_duration_s=120)
        can, reason = evaluator.can_backfill(job, node, now=now)
        assert can is False
        assert "would_delay_reservation" in reason

    def test_can_backfill_fits_before_reservation(self):
        mgr = ReservationManager()
        evaluator = BackfillEvaluator(mgr)
        now = _utcnow()
        node = _make_node()

        mgr.create_reservation(
            _make_job(job_id="reserved-1", priority=90),
            node,
            start_at=now + datetime.timedelta(seconds=120),
            estimated_duration_s=300,
        )

        job = _make_job(job_id="backfill-1", estimated_duration_s=60)
        can, reason = evaluator.can_backfill(job, node, now=now)
        assert can is True

    def test_duration_exceeds_limit(self):
        cfg = BackfillConfig(max_backfill_duration_s=60)
        mgr = ReservationManager(config=cfg)
        evaluator = BackfillEvaluator(mgr)

        job = _make_job(estimated_duration_s=120)
        node = _make_node()
        can, reason = evaluator.can_backfill(job, node, now=_utcnow())
        assert can is False
        assert "duration_exceeds_limit" in reason

    def test_backfill_disabled_passes_all(self):
        cfg = BackfillConfig(enabled=False)
        mgr = ReservationManager(config=cfg)
        evaluator = BackfillEvaluator(mgr)

        job = _make_job(estimated_duration_s=9999)
        node = _make_node()
        can, reason = evaluator.can_backfill(job, node, now=_utcnow())
        assert can is True


# =====================================================================
# ReservationHonorGate
# =====================================================================


class TestReservationHonorGate:
    def test_no_reservation_passes(self):
        mgr = ReservationManager()
        gate = ReservationHonorGate(mgr)
        job = _make_job()
        ctx = _make_ctx()

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert reason == ""

    def test_imminent_reservation_boosts(self):
        mgr = ReservationManager()
        now = _utcnow()
        node = _make_node()
        job = _make_job(job_id="j1", priority=70)

        mgr.create_reservation(
            job,
            node,
            start_at=now + datetime.timedelta(seconds=30),
            estimated_duration_s=300,
        )

        gate = ReservationHonorGate(mgr)
        ctx = _make_ctx(now)

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert "reservation_boost" in reason
        assert job.priority > 70  # Was boosted

    def test_distant_reservation_no_boost(self):
        mgr = ReservationManager()
        now = _utcnow()
        node = _make_node()
        job = _make_job(job_id="j1", priority=70)

        mgr.create_reservation(
            job,
            node,
            start_at=now + datetime.timedelta(seconds=600),
            estimated_duration_s=300,
        )

        gate = ReservationHonorGate(mgr)
        ctx = _make_ctx(now)

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert reason == ""
        assert job.priority == 70  # Unchanged


# =====================================================================
# BackfillGate
# =====================================================================


class TestBackfillGate:
    def test_low_priority_marked_as_backfill(self):
        mgr = ReservationManager()
        gate = BackfillGate(mgr)
        job = _make_job(job_id="j1", priority=30)
        ctx = _make_ctx()

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert reason == "backfill_candidate"
        assert "j1" in ctx.data["_backfill_eligible"]

    def test_high_priority_not_backfill(self):
        mgr = ReservationManager()
        gate = BackfillGate(mgr)
        job = _make_job(priority=80)
        ctx = _make_ctx()

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert reason == ""

    def test_disabled_passes_all(self):
        cfg = BackfillConfig(enabled=False)
        mgr = ReservationManager(config=cfg)
        gate = BackfillGate(mgr)
        job = _make_job(priority=10)
        ctx = _make_ctx()

        ok, reason = gate.evaluate(job, ctx)
        assert ok is True
        assert "_backfill_eligible" not in ctx.data


# =====================================================================
# Singleton lifecycle
# =====================================================================


class TestSingleton:
    def test_get_returns_same_instance(self):
        mgr1 = get_reservation_manager()
        mgr2 = get_reservation_manager()
        assert mgr1 is mgr2

    def test_reset_creates_new_instance(self):
        mgr1 = get_reservation_manager()
        reset_reservation_manager()
        mgr2 = get_reservation_manager()
        assert mgr1 is not mgr2
