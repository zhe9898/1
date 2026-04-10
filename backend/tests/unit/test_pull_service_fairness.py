from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.control_plane.adapters.jobs.models import JobPullRequest
from backend.control_plane.adapters.jobs.pull_service import (
    PullJobsDependencies,
    _build_candidate_context,
    _build_quota_context,
    _get_starvation_rescue_limit,
)
from backend.kernel.policy.types import DispatchConfig
from backend.models.job import Job


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _all_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


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


def _deps() -> PullJobsDependencies:
    return PullJobsDependencies(
        authenticate_node_request=AsyncMock(),
        acquire_transaction_advisory_locks=AsyncMock(),
        get_reservation_manager=MagicMock(),
        get_governance_facade=MagicMock(),
        maybe_schedule_deadline_dlq_sweep=MagicMock(),
        get_failure_control_plane=MagicMock(),
        load_node_metrics=AsyncMock(),
        build_snapshots=MagicMock(),
        build_job_concurrency_window=MagicMock(),
        load_recent_failed_job_ids=AsyncMock(return_value=set()),
        async_build_time_budgeted_placement_plan=AsyncMock(),
        select_jobs_for_node=MagicMock(),
        append_log=AsyncMock(),
        get_current_attempt=AsyncMock(),
        publish_control_event=AsyncMock(),
        to_response=MagicMock(),
        to_lease_response=MagicMock(),
        utcnow=_utcnow,
    )


def test_starvation_rescue_limit_scales_with_pull_size() -> None:
    dispatch = DispatchConfig(
        starvation_rescue_multiplier=4,
        starvation_rescue_min=16,
        starvation_rescue_max=128,
    )
    assert _get_starvation_rescue_limit(payload_limit=1, dispatch_config=dispatch) == 16
    assert _get_starvation_rescue_limit(payload_limit=8, dispatch_config=dispatch) == 32
    assert _get_starvation_rescue_limit(payload_limit=64, dispatch_config=dispatch) == 128


@pytest.mark.asyncio
async def test_build_candidate_context_adds_starvation_rescue_candidates_when_primary_window_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _utcnow()
    primary_candidates = [
        _job(job_id="job-high", priority=90, created_at=now - datetime.timedelta(minutes=5)),
        _job(job_id="job-mid", priority=70, created_at=now - datetime.timedelta(minutes=4)),
    ]
    rescue_candidates = [
        _job(job_id="job-starved", priority=10, created_at=now - datetime.timedelta(hours=3)),
    ]

    query_primary = AsyncMock(return_value=primary_candidates)
    query_rescue = AsyncMock(return_value=rescue_candidates)

    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._query_dispatch_candidates", query_primary)
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._query_starved_dispatch_candidates", query_rescue)
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._filter_dispatch_candidates",
        AsyncMock(side_effect=lambda jobs, **_: jobs),
    )
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._load_completed_dependency_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._load_parent_jobs",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._get_dispatch_config",
        lambda: DispatchConfig(
            starvation_rescue_multiplier=2,
            starvation_rescue_min=2,
            starvation_rescue_max=4,
        ),
    )
    monkeypatch.setattr("backend.runtime.scheduling.business_scheduling.apply_business_filters", lambda jobs, **_: jobs)

    db = AsyncMock()
    db.execute.return_value = _all_result([])
    audit = SimpleNamespace(context={}, candidates_count=0)

    context = await _build_candidate_context(
        db=db,
        payload=JobPullRequest(tenant_id="default", node_id="node-a", limit=1, accepted_kinds=["connector.invoke"]),
        now=now,
        node_snapshot=SimpleNamespace(executor="go-native", max_concurrency=8, active_lease_count=0),
        governance=MagicMock(),
        failure_control_plane=MagicMock(),
        ff_executor_val=False,
        accepted_kinds={"connector.invoke"},
        candidate_limit=2,
        active_node_snapshots=[],
        audit=audit,
        deps=_deps(),
    )

    assert [job.job_id for job in context.candidates] == ["job-high", "job-mid", "job-starved"]
    query_rescue.assert_awaited_once()
    assert audit.context["candidate_window"]["starvation_rescue_added"] == 1
    assert audit.context["candidate_window"]["total_candidates_before_filters"] == 3


@pytest.mark.asyncio
async def test_build_candidate_context_skips_starvation_rescue_when_primary_window_is_not_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _utcnow()
    primary_candidates = [
        _job(job_id="job-high", priority=90, created_at=now - datetime.timedelta(minutes=5)),
    ]

    query_primary = AsyncMock(return_value=primary_candidates)
    query_rescue = AsyncMock(return_value=[_job(job_id="job-starved", created_at=now - datetime.timedelta(hours=3))])

    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._query_dispatch_candidates", query_primary)
    monkeypatch.setattr("backend.control_plane.adapters.jobs.pull_service._query_starved_dispatch_candidates", query_rescue)
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._filter_dispatch_candidates",
        AsyncMock(side_effect=lambda jobs, **_: jobs),
    )
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._load_completed_dependency_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._load_parent_jobs",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "backend.control_plane.adapters.jobs.pull_service._get_dispatch_config",
        lambda: DispatchConfig(
            starvation_rescue_multiplier=2,
            starvation_rescue_min=2,
            starvation_rescue_max=4,
        ),
    )
    monkeypatch.setattr("backend.runtime.scheduling.business_scheduling.apply_business_filters", lambda jobs, **_: jobs)

    db = AsyncMock()
    db.execute.return_value = _all_result([])
    audit = SimpleNamespace(context={}, candidates_count=0)

    context = await _build_candidate_context(
        db=db,
        payload=JobPullRequest(tenant_id="default", node_id="node-a", limit=1, accepted_kinds=["connector.invoke"]),
        now=now,
        node_snapshot=SimpleNamespace(executor="go-native", max_concurrency=8, active_lease_count=0),
        governance=MagicMock(),
        failure_control_plane=MagicMock(),
        ff_executor_val=False,
        accepted_kinds={"connector.invoke"},
        candidate_limit=2,
        active_node_snapshots=[],
        audit=audit,
        deps=_deps(),
    )

    assert [job.job_id for job in context.candidates] == ["job-high"]
    query_rescue.assert_not_awaited()
    assert audit.context["candidate_window"]["starvation_rescue_added"] == 0
    assert audit.context["candidate_window"]["starvation_rescue_limit"] == 0


def test_build_quota_context_raises_when_quota_contract_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_unavailable(_leased_jobs: list[Job]) -> dict[str, object]:
        raise RuntimeError("quota contract unavailable")

    monkeypatch.setattr("backend.runtime.scheduling.quota_aware_scheduling.build_quota_accounts", _raise_unavailable)

    with pytest.raises(RuntimeError, match="quota contract unavailable"):
        _build_quota_context([])
