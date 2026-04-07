"""Tests for the pluggable scheduling framework (scheduling_framework.py)."""

from __future__ import annotations

import datetime

import pytest

from backend.core.scheduling_framework import (
    ConstraintFilterAdapter,
    ConstraintScoreAdapter,
    FilterPlugin,
    PluginResult,
    PluginStatus,
    PreFilterPlugin,
    QueueSortPlugin,
    ReservePlugin,
    SchedulingPipeline,
    SchedulingProfile,
    ScorePlugin,
    build_profile_from_engine,
    get_profile,
    list_profiles,
    register_profile,
)

# ── Helpers ──────────────────────────────────────────────────────────


class _FakeJob:
    def __init__(self, job_id: str, priority: int = 50, kind: str = "shell.exec"):
        self.job_id = job_id
        self.priority = priority
        self.kind = kind
        self.status = "pending"
        self.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
        self.deadline_at = None
        self.sla_seconds = None
        self.parent_job_id = None
        self.gang_id = None
        self.depends_on = []
        self.connector_id = None
        self.tenant_id = "default"


def _make_ctx(available_slots: int = 10):
    from backend.core.scheduling_constraints import SchedulingContext

    return SchedulingContext(
        now=datetime.datetime.now(datetime.timezone.utc),
        completed_job_ids=set(),
        available_slots=available_slots,
        parent_jobs={},
    )


# ── QueueSort phase ─────────────────────────────────────────────────


class PriorityDescSort(QueueSortPlugin):
    name = "priority_desc"

    def less(self, a, b) -> bool:
        return (a.priority or 0) > (b.priority or 0)


def test_queue_sort():
    profile = SchedulingProfile(name="test", queue_sort=[PriorityDescSort()])
    pipe = SchedulingPipeline(profile)

    jobs = [_FakeJob("j1", 10), _FakeJob("j2", 90), _FakeJob("j3", 50)]
    sorted_jobs = pipe.run_queue_sort(jobs)
    assert [j.priority for j in sorted_jobs] == [90, 50, 10]


def test_queue_sort_empty():
    profile = SchedulingProfile(name="test")
    pipe = SchedulingPipeline(profile)
    jobs = [_FakeJob("j1", 10)]
    assert pipe.run_queue_sort(jobs) == jobs


# ── PreFilter phase ──────────────────────────────────────────────────


class RejectAllPreFilter(PreFilterPlugin):
    name = "reject_all"

    def pre_filter(self, ctx):
        return PluginResult(status=PluginStatus.REJECT, reason="testing")


class PassPreFilter(PreFilterPlugin):
    name = "pass_all"

    def pre_filter(self, ctx):
        return PluginResult(status=PluginStatus.SUCCESS)


def test_pre_filter_reject():
    profile = SchedulingProfile(name="test", pre_filters=[RejectAllPreFilter()])
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()
    result = pipe.run_pre_filter(ctx)
    assert result.status == PluginStatus.REJECT


def test_pre_filter_pass():
    profile = SchedulingProfile(name="test", pre_filters=[PassPreFilter()])
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()
    result = pipe.run_pre_filter(ctx)
    assert result.status == PluginStatus.SUCCESS


# ── Filter phase ─────────────────────────────────────────────────────


class HighPriorityOnlyFilter(FilterPlugin):
    name = "high_priority_only"

    def filter(self, job, ctx):
        if (job.priority or 0) < 50:
            return PluginResult(status=PluginStatus.REJECT, reason="low_priority")
        return PluginResult(status=PluginStatus.SUCCESS)


def test_filter_drops_low_priority():
    profile = SchedulingProfile(name="test", filters=[HighPriorityOnlyFilter()])
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()

    jobs = [_FakeJob("j1", 10), _FakeJob("j2", 90), _FakeJob("j3", 50)]
    accepted, rejected = pipe.run_filter(jobs, ctx)
    assert len(accepted) == 2
    assert len(rejected) == 1
    assert rejected[0][0].job_id == "j1"


# ── Score phase ──────────────────────────────────────────────────────


class BonusScore(ScorePlugin):
    name = "bonus"

    def score(self, job, ctx):
        delta = 10 if (job.priority or 0) > 70 else 0
        return PluginResult(status=PluginStatus.SUCCESS, score_delta=delta)


def test_score_plugin():
    profile = SchedulingProfile(name="test", scorers=[BonusScore()])
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()

    jobs = [_FakeJob("j1", 30), _FakeJob("j2", 80)]
    deltas = pipe.run_score(jobs, ctx)
    assert deltas["j1"] == 0
    assert deltas["j2"] == 10


# ── Reserve phase ────────────────────────────────────────────────────


class TrackingReservePlugin(ReservePlugin):
    name = "tracking"

    def __init__(self):
        self.reserved = []
        self.unreserved = []

    def reserve(self, job, ctx):
        self.reserved.append(job.job_id)
        return PluginResult(status=PluginStatus.SUCCESS)

    def unreserve(self, job, ctx):
        self.unreserved.append(job.job_id)


class FailingReservePlugin(ReservePlugin):
    name = "failing"

    def reserve(self, job, ctx):
        return PluginResult(status=PluginStatus.REJECT, reason="no_capacity")

    def unreserve(self, job, ctx):
        pass


def test_reserve_success():
    plugin = TrackingReservePlugin()
    profile = SchedulingProfile(name="test", reservers=[plugin])
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()
    job = _FakeJob("j1")

    result = pipe.run_reserve(job, ctx)
    assert result.status == PluginStatus.SUCCESS
    assert "j1" in plugin.reserved


def test_reserve_rollback():
    first = TrackingReservePlugin()
    second = FailingReservePlugin()
    profile = SchedulingProfile(name="test", reservers=[first, second])
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()
    job = _FakeJob("j1")

    result = pipe.run_reserve(job, ctx)
    assert result.status == PluginStatus.REJECT
    assert "j1" in first.unreserved


# ── Full pipeline ────────────────────────────────────────────────────


def test_full_pipeline():
    profile = SchedulingProfile(
        name="test",
        queue_sort=[PriorityDescSort()],
        filters=[HighPriorityOnlyFilter()],
        scorers=[BonusScore()],
    )
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()

    jobs = [_FakeJob("j1", 10), _FakeJob("j2", 90), _FakeJob("j3", 60)]
    result = pipe.run_full(jobs, ctx)
    assert len(result) == 2
    # j2 should have boosted priority (90 + 10 = 100)
    j2 = next(j for j in result if j.job_id == "j2")
    assert j2.priority == 100


def test_full_pipeline_pre_filter_reject():
    profile = SchedulingProfile(
        name="test",
        pre_filters=[RejectAllPreFilter()],
        filters=[HighPriorityOnlyFilter()],
    )
    pipe = SchedulingPipeline(profile)
    ctx = _make_ctx()
    jobs = [_FakeJob("j1", 90)]
    result = pipe.run_full(jobs, ctx)
    assert len(result) == 0


# ── Profile registry ────────────────────────────────────────────────


def test_register_and_get_profile():
    profile = SchedulingProfile(name="test_profile_xyz")
    register_profile(profile)
    assert "test_profile_xyz" in list_profiles()
    assert get_profile("test_profile_xyz").name == "test_profile_xyz"


def test_get_missing_profile():
    p = get_profile("nonexistent_abc")
    assert p.name == "nonexistent_abc"
    assert p.filters == []


# ── Constraint adapters ─────────────────────────────────────────────


def test_constraint_filter_adapter():
    from backend.core.scheduling_constraints import DeadlineExpiryGate

    gate = DeadlineExpiryGate()
    adapter = ConstraintFilterAdapter(gate)
    assert adapter.name == "constraint:deadline_expiry"

    ctx = _make_ctx()
    job = _FakeJob("j1")
    result = adapter.filter(job, ctx)
    assert result.status == PluginStatus.SUCCESS


def test_constraint_score_adapter():
    from backend.core.scheduling_constraints import PriorityBoostModifier

    modifier = PriorityBoostModifier()
    adapter = ConstraintScoreAdapter(modifier)
    assert adapter.name == "score:priority_boost"

    ctx = _make_ctx()
    job = _FakeJob("j1")
    result = adapter.score(job, ctx)
    assert result.status == PluginStatus.SUCCESS


def test_build_profile_from_engine():
    profile = build_profile_from_engine()
    assert profile.name == "default"
    # Should have at least the built-in gates as filters
    assert len(profile.filters) > 0


def test_constraint_adapter_type_error():
    with pytest.raises(TypeError):
        ConstraintFilterAdapter("not_a_constraint")


# ── Pipeline profile_name property ──────────────────────────────────


def test_pipeline_profile_name():
    profile = SchedulingProfile(name="batch_v2")
    pipe = SchedulingPipeline(profile)
    assert pipe.profile_name == "batch_v2"
