from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.api.reservations import (
    BackfillWindowResponse,
    ReservationCancelRequest,
    ReservationCreateRequest,
    cancel_reservation,
    create_reservation,
    get_backfill_window,
    get_reservation,
    get_reservation_stats,
    list_reservations,
)
from backend.kernel.scheduling.backfill_scheduling import get_reservation_manager, reset_reservation_manager
from backend.models.job import Job
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC).replace(tzinfo=None)


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
        priority=90,
        queue_class="interactive",
        worker_pool="interactive",
        target_os=None,
        target_arch=None,
        target_executor=None,
        required_capabilities=[],
        target_zone=None,
        required_cpu_cores=4,
        required_memory_mb=2048,
        required_gpu_vram_mb=0,
        required_storage_mb=0,
        timeout_seconds=300,
        max_retries=0,
        retry_count=0,
        attempt_count=0,
        failure_category=None,
        estimated_duration_s=300,
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


def _node(**overrides: object) -> Node:
    now = _utcnow()
    node = Node(
        tenant_id="default",
        node_id="node-1",
        name="runner-1",
        node_type="runner",
        address=None,
        profile="go-runner",
        executor="go-native",
        os="linux",
        arch="amd64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        agent_version=None,
        max_concurrency=1,
        cpu_cores=8,
        memory_mb=16384,
        gpu_vram_mb=0,
        storage_mb=10240,
        drain_status="active",
        health_reason=None,
        drain_until=None,
        auth_token_hash=None,
        auth_token_version=1,
        enrollment_status="approved",
        status="online",
        capabilities=["connector.invoke"],
        accepted_kinds=["connector.invoke"],
        worker_pools=["interactive"],
        network_latency_ms=None,
        bandwidth_mbps=None,
        cached_data_keys=[],
        power_capacity_watts=None,
        current_power_watts=None,
        thermal_state=None,
        cloud_connectivity=None,
        metadata_json={},
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    return result


def _all_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


@pytest.fixture(autouse=True)
def _reset_reservations() -> None:
    reset_reservation_manager()
    yield
    reset_reservation_manager()


@pytest.mark.asyncio
async def test_create_get_and_cancel_manual_reservation() -> None:
    db = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(_job(job_id="job-manual")),
        _scalar_result(_node(node_id="node-manual")),
        _all_result([]),
    ]

    created = await create_reservation(
        ReservationCreateRequest(
            job_id="job-manual",
            node_id="node-manual",
            start_at=_utcnow() + datetime.timedelta(minutes=5),
            estimated_duration_s=600,
            reason="manual reserve",
        ),
        current_user={"role": "admin", "tenant_id": "default"},
        db=db,
        redis=None,
    )

    fetched = await get_reservation("job-manual", current_user={"tenant_id": "default"})

    canceled = await cancel_reservation(
        "job-manual",
        ReservationCancelRequest(reason="operator cleared"),
        current_user={"role": "admin", "tenant_id": "default"},
        redis=None,
    )

    assert created.job_id == "job-manual"
    assert created.node_id == "node-manual"
    assert fetched.job_id == "job-manual"
    assert canceled.job_id == "job-manual"
    assert get_reservation_manager().get_reservation("job-manual") is None


@pytest.mark.asyncio
async def test_list_and_stats_are_tenant_scoped() -> None:
    mgr = get_reservation_manager()
    mgr.create_reservation(_job(job_id="job-a", tenant_id="default"), _node(node_id="node-a", tenant_id="default"), start_at=_utcnow())
    mgr.create_reservation(_job(job_id="job-b", tenant_id="tenant-b"), _node(node_id="node-a", tenant_id="tenant-b"), start_at=_utcnow())

    listed = await list_reservations(node_id=None, after=None, current_user={"tenant_id": "default"})
    stats = await get_reservation_stats(current_user={"tenant_id": "default"})

    assert [item.job_id for item in listed] == ["job-a"]
    assert stats.active_reservations == 1
    assert stats.node_counts == {"node-a": 1}
    assert stats.store_backend == "memory"


@pytest.mark.asyncio
async def test_backfill_window_uses_current_active_leases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.api.reservations._utcnow", _utcnow)
    mgr = get_reservation_manager()
    node = _node(node_id="node-hot")
    leased_job = _job(
        job_id="job-running",
        status="leased",
        node_id="node-hot",
        started_at=_utcnow(),
        estimated_duration_s=300,
        leased_until=_utcnow() + datetime.timedelta(minutes=10),
    )
    mgr.create_reservation(
        _job(job_id="job-reserved", tenant_id="default", estimated_duration_s=300),
        node,
        start_at=_utcnow() + datetime.timedelta(minutes=10),
    )
    db = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(node),
        _all_result([leased_job]),
    ]

    response = await get_backfill_window(
        "node-hot",
        required_duration_s=120,
        current_user={"tenant_id": "default"},
        db=db,
    )

    assert isinstance(response, BackfillWindowResponse)
    assert response.has_window is True
    assert response.estimated_node_available_at == _utcnow() + datetime.timedelta(minutes=10)
    assert response.window_start_at == _utcnow() + datetime.timedelta(minutes=15)


@pytest.mark.asyncio
async def test_cancel_rejects_cross_tenant_access() -> None:
    mgr = get_reservation_manager()
    mgr.create_reservation(_job(job_id="job-cross", tenant_id="tenant-a"), _node(node_id="node-a", tenant_id="tenant-a"), start_at=_utcnow())

    with pytest.raises(HTTPException) as exc:
        await cancel_reservation(
            "job-cross",
            ReservationCancelRequest(reason="nope"),
            current_user={"role": "admin", "tenant_id": "tenant-b"},
            redis=None,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_reservation_uses_current_active_leases(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api import reservations as reservations_api

    captured: dict[str, int] = {}
    original_builder = reservations_api.build_node_snapshot

    def _capture_build_node_snapshot(node: Node, active_lease_count: int, reliability_score: float):  # type: ignore[no-untyped-def]
        captured["active_lease_count"] = active_lease_count
        return original_builder(node, active_lease_count=active_lease_count, reliability_score=reliability_score)

    monkeypatch.setattr(reservations_api, "build_node_snapshot", _capture_build_node_snapshot)

    db = AsyncMock()
    db.execute.side_effect = [
        _scalar_result(_job(job_id="job-active-check")),
        _scalar_result(_node(node_id="node-active-check")),
        _all_result(
            [
                _job(job_id="lease-1", node_id="node-active-check", status="leased"),
                _job(job_id="lease-2", node_id="node-active-check", status="leased"),
            ]
        ),
    ]

    await create_reservation(
        ReservationCreateRequest(
            job_id="job-active-check",
            node_id="node-active-check",
            start_at=_utcnow() + datetime.timedelta(minutes=5),
            estimated_duration_s=300,
            reason="active lease check",
        ),
        current_user={"role": "admin", "tenant_id": "default"},
        db=db,
        redis=None,
    )

    assert captured["active_lease_count"] == 2
