"""Tests for business scheduling hard constraints.

Covers the gates and helpers in business_scheduling.py that are now
wired as runtime enforcement in the dispatch chain.
"""

from __future__ import annotations

import datetime

from backend.core.business_scheduling import (
    apply_business_filters,
    calculate_boosted_priority,
    calculate_sla_breach_risk,
    find_preemption_candidates,
    should_preempt_for_job,
)
from backend.models.job import Job


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _job(**overrides: object) -> Job:
    now = _utcnow()
    job = Job(
        tenant_id="default",
        job_id="job-1",
        kind="connector.invoke",
        status="pending",
        node_id=None,
        connector_id=None,
        idempotency_key=None,
        priority=50,
        target_os=None,
        target_arch=None,
        required_capabilities=[],
        target_zone=None,
        timeout_seconds=300,
        max_retries=0,
        retry_count=0,
        estimated_duration_s=None,
        source="console",
        created_by="tester",
        payload={"hello": "world"},
        result=None,
        error_message=None,
        lease_seconds=30,
        lease_token=None,
        attempt=0,
        leased_until=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return job


# ── Deadline expiry gate ─────────────────────────────────────────────


class TestDeadlineExpiryGate:
    def test_expired_deadline_filtered_out(self) -> None:
        now = _utcnow()
        expired = _job(job_id="expired", deadline_at=now - datetime.timedelta(hours=1))
        alive = _job(job_id="alive", deadline_at=now + datetime.timedelta(hours=1))
        no_dl = _job(job_id="no-deadline", deadline_at=None)

        result = apply_business_filters(
            [expired, alive, no_dl],
            completed_job_ids=set(),
            available_slots=10,
            parent_jobs={},
            now=now,
        )
        ids = [j.job_id for j in result]
        assert "expired" not in ids
        assert "alive" in ids
        assert "no-deadline" in ids

    def test_exactly_at_deadline_filtered(self) -> None:
        now = _utcnow()
        exact = _job(job_id="exact", deadline_at=now)

        result = apply_business_filters(
            [exact],
            completed_job_ids=set(),
            available_slots=10,
            parent_jobs={},
            now=now,
        )
        assert len(result) == 0


# ── Dependency gate ──────────────────────────────────────────────────


class TestDependencyGate:
    def test_unsatisfied_deps_blocked(self) -> None:
        now = _utcnow()
        blocked = _job(job_id="blocked")
        blocked.depends_on = ["dep-1", "dep-2"]
        free = _job(job_id="free")

        result = apply_business_filters(
            [blocked, free],
            completed_job_ids={"dep-1"},
            available_slots=10,
            parent_jobs={},
            now=now,
        )
        ids = [j.job_id for j in result]
        assert "blocked" not in ids
        assert "free" in ids

    def test_all_deps_satisfied_passes(self) -> None:
        now = _utcnow()
        job = _job(job_id="ready")
        job.depends_on = ["dep-1"]

        result = apply_business_filters(
            [job],
            completed_job_ids={"dep-1"},
            available_slots=10,
            parent_jobs={},
            now=now,
        )
        assert len(result) == 1


# ── Priority boost ───────────────────────────────────────────────────


class TestEffectivePriority:
    def test_deadline_urgency_boost(self) -> None:
        now = _utcnow()
        urgent = _job(
            job_id="urgent",
            priority=50,
            deadline_at=now + datetime.timedelta(minutes=30),
        )
        boosted = calculate_boosted_priority(urgent, now=now)
        assert boosted > 50

    def test_sla_boost(self) -> None:
        now = _utcnow()
        old = _job(
            job_id="old",
            priority=50,
            created_at=now - datetime.timedelta(hours=2),
        )
        old.sla_seconds = 7200  # 2 hour SLA, 2 hours elapsed → 100% consumed
        boosted = calculate_boosted_priority(old, now=now)
        assert boosted > 50

    def test_parent_inheritance(self) -> None:
        now = _utcnow()
        parent = _job(job_id="parent", priority=90)
        child = _job(job_id="child", priority=40)
        child.parent_job_id = "parent"
        boosted = calculate_boosted_priority(child, now=now, parent_jobs={"parent": parent})
        assert boosted > 40


# ── SLA breach risk ──────────────────────────────────────────────────


class TestSLABreachRisk:
    def test_no_sla_returns_none(self) -> None:
        now = _utcnow()
        job = _job()
        risk, level = calculate_sla_breach_risk(job, now=now)
        assert level == "none"
        assert risk == 0.0

    def test_breached_sla(self) -> None:
        now = _utcnow()
        job = _job(created_at=now - datetime.timedelta(hours=3))
        job.sla_seconds = 3600  # 1 hour SLA, 3 hours old
        risk, level = calculate_sla_breach_risk(job, now=now)
        assert level == "breached"


# ── Preemption ───────────────────────────────────────────────────────


class TestPreemption:
    def test_preempt_low_priority_for_urgent(self) -> None:
        now = _utcnow()
        high = _job(
            job_id="high",
            priority=95,
            deadline_at=now + datetime.timedelta(minutes=30),
        )
        high.sla_seconds = 3600
        low = _job(
            job_id="low",
            priority=10,
            started_at=now - datetime.timedelta(minutes=1),
            status="leased",
        )
        should, reason = should_preempt_for_job(high, low, now=now)
        assert should is True
        assert "preempt" in reason

    def test_no_preempt_non_preemptible(self) -> None:
        now = _utcnow()
        high = _job(job_id="high", priority=95)
        high.sla_seconds = 3600
        low = _job(job_id="low", priority=10)
        low.preemptible = False
        should, _ = should_preempt_for_job(high, low, now=now)
        assert should is False

    def test_no_preempt_small_priority_diff(self) -> None:
        now = _utcnow()
        high = _job(job_id="high", priority=60)
        high.sla_seconds = 3600
        low = _job(job_id="low", priority=40)
        should, _ = should_preempt_for_job(high, low, now=now)
        assert should is False

    def test_no_preempt_long_running(self) -> None:
        now = _utcnow()
        high = _job(job_id="high", priority=95)
        high.sla_seconds = 3600
        low = _job(
            job_id="low",
            priority=10,
            started_at=now - datetime.timedelta(minutes=10),
        )
        should, _ = should_preempt_for_job(high, low, now=now)
        assert should is False


# ── find_preemption_candidates ───────────────────────────────────────


class TestFindPreemptionCandidates:
    def test_finds_valid_pair(self) -> None:
        now = _utcnow()
        urgent = _job(
            job_id="urgent",
            priority=95,
            deadline_at=now + datetime.timedelta(minutes=30),
        )
        urgent.sla_seconds = 3600
        victim = _job(
            job_id="victim",
            priority=10,
            started_at=now - datetime.timedelta(minutes=1),
            status="leased",
        )
        results = find_preemption_candidates([urgent], [victim], now=now)
        assert len(results) == 1
        assert results[0][0].job_id == "urgent"
        assert results[0][1].job_id == "victim"

    def test_empty_when_no_eligible(self) -> None:
        now = _utcnow()
        urgent = _job(job_id="urgent", priority=55)
        victim = _job(job_id="victim", priority=50, status="leased")
        results = find_preemption_candidates([urgent], [victim], now=now)
        assert len(results) == 0

    def test_each_victim_claimed_once(self) -> None:
        now = _utcnow()
        u1 = _job(job_id="u1", priority=95, deadline_at=now + datetime.timedelta(minutes=30))
        u1.sla_seconds = 3600
        u2 = _job(job_id="u2", priority=92, deadline_at=now + datetime.timedelta(minutes=30))
        u2.sla_seconds = 3600
        v1 = _job(job_id="v1", priority=10, started_at=now - datetime.timedelta(minutes=1), status="leased")
        results = find_preemption_candidates([u1, u2], [v1], now=now)
        # Only one preemption — victim can only be claimed once
        assert len(results) == 1
