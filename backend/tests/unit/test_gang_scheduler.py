"""Tests for gang scheduling coordinator (gang_scheduler.py)."""

from __future__ import annotations

import datetime

from backend.core.gang_scheduler import (
    GangCoordinator,
    GangGroup,
    GangPermitPlugin,
    reset_gang_coordinator,
    solve_gang_placement,
)


# ── Helpers ──────────────────────────────────────────────────────────


class _FakeJob:
    def __init__(
        self,
        job_id: str,
        gang_id: str | None = None,
        priority: int = 50,
        gang_min_available: int = 0,
    ):
        self.job_id = job_id
        self.gang_id = gang_id
        self.gang_min_available = gang_min_available
        self.priority = priority
        self.kind = "container.run"
        self.status = "pending"
        self.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
        self.deadline_at = None
        self.sla_seconds = None
        self.parent_job_id = None
        self.depends_on = []
        self.connector_id = None
        self.tenant_id = "default"


class _FakeNode:
    def __init__(self, node_id: str, max_concurrency: int = 4, active_lease_count: int = 0):
        self.node_id = node_id
        self.max_concurrency = max_concurrency
        self.active_lease_count = active_lease_count


# ── GangGroup tests ─────────────────────────────────────────────────


def test_gang_group_required_count_default():
    """min_available=0 means all members required."""
    g = GangGroup(gang_id="g1")
    g.members = [_FakeJob("j1", "g1"), _FakeJob("j2", "g1"), _FakeJob("j3", "g1")]
    assert g.required_count == 3


def test_gang_group_required_count_min_available():
    g = GangGroup(gang_id="g1", min_available=2)
    g.members = [_FakeJob("j1", "g1"), _FakeJob("j2", "g1"), _FakeJob("j3", "g1")]
    assert g.required_count == 2


def test_gang_group_satisfiable():
    g = GangGroup(gang_id="g1", min_available=2)
    g.members = [_FakeJob("j1", "g1"), _FakeJob("j2", "g1")]
    g.placed_count = 2
    assert g.is_satisfiable is True
    assert g.pending_count == 0


def test_gang_group_not_satisfiable():
    g = GangGroup(gang_id="g1")
    g.members = [_FakeJob("j1", "g1"), _FakeJob("j2", "g1"), _FakeJob("j3", "g1")]
    g.placed_count = 1
    assert g.is_satisfiable is False
    assert g.pending_count == 2


# ── GangCoordinator tests ───────────────────────────────────────────


def test_coordinator_register_and_group():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", "gang-a")
    j2 = _FakeJob("j2", "gang-a")
    j3 = _FakeJob("j3", None)

    coord.register(j1)
    coord.register(j2)
    coord.register(j3)

    group = coord.get_group("gang-a")
    assert group is not None
    assert len(group.members) == 2
    assert coord.get_group("nonexistent") is None


def test_coordinator_mark_placed():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", "gang-a")
    j2 = _FakeJob("j2", "gang-a")
    coord.register(j1)
    coord.register(j2)

    coord.mark_placed(j1, "node-1")
    assert coord.get_group("gang-a").placed_count == 1

    coord.mark_placed(j2, "node-2")
    assert coord.get_group("gang-a").placed_count == 2
    assert coord.get_group("gang-a").is_satisfiable is True


def test_coordinator_mark_placed_idempotent():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", "gang-a")
    coord.register(j1)

    coord.mark_placed(j1, "node-1")
    coord.mark_placed(j1, "node-1")  # duplicate
    assert coord.get_group("gang-a").placed_count == 1


def test_coordinator_is_gang_ready():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", "gang-a")
    j2 = _FakeJob("j2", "gang-a")
    coord.register(j1)
    coord.register(j2)

    # Enough slots
    assert coord.is_gang_ready(j1, total_available_slots=10) is True
    # Not enough slots
    assert coord.is_gang_ready(j1, total_available_slots=1) is False


def test_coordinator_is_gang_ready_non_gang():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", None)
    assert coord.is_gang_ready(j1, total_available_slots=0) is True


def test_coordinator_is_gang_ready_member_not_pending():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", "gang-a")
    j2 = _FakeJob("j2", "gang-a")
    j2.status = "failed"
    coord.register(j1)
    coord.register(j2)

    assert coord.is_gang_ready(j1, total_available_slots=10) is False


def test_coordinator_ready_and_unsatisfied_groups():
    coord = GangCoordinator()
    # Gang A: fully placed
    for i in range(3):
        j = _FakeJob(f"a-{i}", "gang-a")
        coord.register(j)
        coord.mark_placed(j, f"node-{i}")

    # Gang B: partially placed
    for i in range(2):
        j = _FakeJob(f"b-{i}", "gang-b")
        coord.register(j)
    coord.mark_placed(_FakeJob("b-0", "gang-b"), "node-0")

    ready = coord.ready_groups()
    unsatisfied = coord.unsatisfied_groups()
    assert len(ready) == 1
    assert ready[0].gang_id == "gang-a"
    assert len(unsatisfied) == 1
    assert unsatisfied[0].gang_id == "gang-b"


def test_coordinator_gang_member_job_ids():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", "gang-a")
    j2 = _FakeJob("j2", "gang-a")
    coord.register(j1)
    coord.register(j2)

    ids = coord.gang_member_job_ids("gang-a")
    assert ids == {"j1", "j2"}
    assert coord.gang_member_job_ids("nonexistent") == set()


# ── Gang placement solver ───────────────────────────────────────────


def test_solve_gang_placement_single_group():
    nodes = [_FakeNode("n1", 4, 0), _FakeNode("n2", 4, 0)]
    group = GangGroup(gang_id="g1")
    group.members = [_FakeJob(f"j{i}", "g1") for i in range(3)]

    result = solve_gang_placement([group], nodes)
    assert "g1" in result
    assert len(result["g1"]) == 3


def test_solve_gang_placement_insufficient_capacity():
    nodes = [_FakeNode("n1", 1, 0)]
    group = GangGroup(gang_id="g1")
    group.members = [_FakeJob(f"j{i}", "g1") for i in range(3)]

    result = solve_gang_placement([group], nodes)
    assert "g1" not in result  # Cannot satisfy


def test_solve_gang_placement_multiple_groups():
    nodes = [_FakeNode("n1", 5, 0), _FakeNode("n2", 5, 0)]

    g1 = GangGroup(gang_id="g1")
    g1.members = [_FakeJob(f"g1-j{i}", "g1") for i in range(3)]

    g2 = GangGroup(gang_id="g2")
    g2.members = [_FakeJob(f"g2-j{i}", "g2") for i in range(4)]

    result = solve_gang_placement([g1, g2], nodes)
    # Both should fit (3 + 4 = 7 ≤ 10)
    assert "g1" in result
    assert "g2" in result


def test_solve_gang_placement_min_available():
    nodes = [_FakeNode("n1", 2, 0)]
    group = GangGroup(gang_id="g1", min_available=2)
    group.members = [_FakeJob(f"j{i}", "g1") for i in range(5)]

    result = solve_gang_placement([group], nodes)
    assert "g1" in result
    assert len(result["g1"]) == 2  # Only need 2 out of 5


def test_solve_gang_placement_respects_existing_load():
    nodes = [_FakeNode("n1", 3, 2)]  # 3 max, 2 active → 1 slot
    group = GangGroup(gang_id="g1")
    group.members = [_FakeJob(f"j{i}", "g1") for i in range(2)]

    result = solve_gang_placement([group], nodes)
    assert "g1" not in result  # Only 1 slot, need 2


# ── GangPermitPlugin ────────────────────────────────────────────────


def test_gang_permit_non_gang_passes():
    coord = GangCoordinator()
    plugin = GangPermitPlugin(coord)
    job = _FakeJob("j1", None)

    from backend.core.scheduling_constraints import SchedulingContext

    ctx = SchedulingContext(
        now=datetime.datetime.now(datetime.timezone.utc),
        completed_job_ids=set(),
        available_slots=10,
        parent_jobs={},
    )
    result = plugin.permit(job, ctx)
    from backend.core.scheduling_framework import PluginStatus

    assert result.status == PluginStatus.SUCCESS


def test_gang_permit_waits_until_all_placed():
    coord = GangCoordinator()
    j1 = _FakeJob("j1", "gang-a")
    j2 = _FakeJob("j2", "gang-a")
    coord.register(j1)
    coord.register(j2)

    plugin = GangPermitPlugin(coord)

    from backend.core.scheduling_constraints import SchedulingContext

    ctx = SchedulingContext(
        now=datetime.datetime.now(datetime.timezone.utc),
        completed_job_ids=set(),
        available_slots=10,
        parent_jobs={},
    )

    # Before placing both
    result = plugin.permit(j1, ctx)
    from backend.core.scheduling_framework import PluginStatus

    assert result.status == PluginStatus.WAIT

    # Place both
    coord.mark_placed(j1, "n1")
    coord.mark_placed(j2, "n2")
    result = plugin.permit(j1, ctx)
    assert result.status == PluginStatus.SUCCESS


# ── Module-level singleton ──────────────────────────────────────────


def test_reset_gang_coordinator():
    c1 = reset_gang_coordinator()
    j = _FakeJob("j1", "g1")
    c1.register(j)

    c2 = reset_gang_coordinator()
    assert c2.get_group("g1") is None  # Fresh coordinator
