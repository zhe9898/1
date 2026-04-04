from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.connectors import list_connectors
from backend.api.jobs import list_jobs
from backend.api.nodes import list_nodes
from backend.models.connector import Connector
from backend.models.job import Job
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _scalars_result(values: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


def _rows_result(values: list[tuple[object, object]]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = values
    return result


def _job(**overrides: object) -> Job:
    now = _utcnow()
    job = Job(
        job_id="job-a",
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
        created_by="admin",
        payload={},
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
        node_id="node-a",
        name="runner-a",
        node_type="runner",
        address=None,
        profile="go-runner",
        executor="go-native",
        os="darwin",
        arch="arm64",
        zone="lab-a",
        protocol_version="runner.v1",
        lease_version="job-lease.v1",
        auth_token_hash=None,
        auth_token_version=1,
        enrollment_status="active",
        status="online",
        capabilities=["connector.invoke"],
        metadata_json={},
        max_concurrency=2,
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def _connector(**overrides: object) -> Connector:
    now = _utcnow()
    connector = Connector(
        connector_id="conn-a",
        name="Connector A",
        kind="http",
        status="configured",
        endpoint=None,
        profile="manual",
        config={},
        last_test_ok=None,
        last_test_status=None,
        last_test_message=None,
        last_test_at=None,
        last_invoke_status=None,
        last_invoke_message=None,
        last_invoke_job_id=None,
        last_invoke_at=None,
        created_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


@pytest.mark.asyncio
async def test_list_jobs_applies_backend_query_filters() -> None:
    now = _utcnow()
    db = AsyncMock()
    db.execute.return_value = _scalars_result(
        [
            _job(job_id="job-running", status="leased", leased_until=now + datetime.timedelta(seconds=30), required_capabilities=["gpu"]),
            _job(job_id="job-failed", status="failed", required_capabilities=["gpu"]),
            _job(job_id="job-pending", status="pending", priority=95, target_zone="lab-b"),
        ]
    )

    response = await list_jobs(
        status="running",
        lease_state="active",
        required_capability="gpu",
        current_user={"sub": "admin"},
        db=db,
    )

    assert [item.job_id for item in response] == ["job-running"]
    assert response[0].status_view.key == "running"
    assert response[0].lease_state_view.key == "active"


@pytest.mark.asyncio
async def test_list_nodes_applies_backend_query_filters() -> None:
    now = _utcnow()
    db = AsyncMock()
    db.execute.side_effect = [
        _scalars_result(
            [
                _node(node_id="node-fresh"),
                _node(node_id="node-stale", last_seen_at=now - datetime.timedelta(minutes=5)),
                _node(node_id="node-draining", drain_status="draining"),
            ]
        ),
        _rows_result([("node-fresh", 0), ("node-stale", 0), ("node-draining", 0)]),
    ]

    response = await list_nodes(
        attention="attention",
        heartbeat_state="stale",
        current_user={"sub": "admin"},
        db=db,
    )

    assert [item.node_id for item in response] == ["node-stale"]
    assert response[0].heartbeat_state_view.key == "stale"
    assert response[0].attention_reason is not None


@pytest.mark.asyncio
async def test_list_connectors_applies_backend_query_filters() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalars_result(
        [
            _connector(connector_id="conn-ok", status="healthy"),
            _connector(connector_id="conn-configured", status="configured"),
            _connector(connector_id="conn-error", status="error", last_test_message="auth failed"),
        ]
    )

    response = await list_connectors(
        attention="attention",
        status="error",
        current_user={"sub": "admin"},
        db=db,
    )

    assert [item.connector_id for item in response] == ["conn-error"]
    assert response[0].status_view.key == "error"
    assert response[0].attention_reason == "auth failed"


@pytest.mark.asyncio
async def test_list_jobs_pagination_default_limit() -> None:
    """list_jobs must not return more rows than the SQL LIMIT (default 100)."""
    db = AsyncMock()
    # Simulate DB returning exactly 3 jobs (SQL LIMIT applied upstream)
    db.execute.return_value = _scalars_result([_job(job_id=f"job-{i}") for i in range(3)])

    response = await list_jobs(
        current_user={"sub": "admin"},
        db=db,
    )

    assert len(response) == 3
    # Verify the SQL query was called with a limit clause (query object is inspected via call args)
    call_args = db.execute.call_args
    compiled = str(call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT" in compiled.upper()
    assert "100" in compiled


@pytest.mark.asyncio
async def test_list_jobs_pagination_custom_limit_and_offset() -> None:
    """list_jobs honours explicit limit and offset parameters."""
    db = AsyncMock()
    db.execute.return_value = _scalars_result([_job(job_id="job-page2")])

    response = await list_jobs(
        limit=10,
        offset=20,
        current_user={"sub": "admin"},
        db=db,
    )

    assert len(response) == 1
    call_args = db.execute.call_args
    compiled = str(call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
    assert "10" in compiled
    assert "20" in compiled


@pytest.mark.asyncio
async def test_list_nodes_pagination_default_limit() -> None:
    """list_nodes must not return more rows than the SQL LIMIT (default 100)."""
    db = AsyncMock()
    db.execute.side_effect = [
        _scalars_result([_node(node_id=f"node-{i}") for i in range(3)]),
        _rows_result([(f"node-{i}", 0) for i in range(3)]),
    ]

    response = await list_nodes(
        current_user={"sub": "admin"},
        db=db,
    )

    assert len(response) == 3
    call_args = db.execute.call_args_list[0]
    compiled = str(call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT" in compiled.upper()
    assert "100" in compiled


@pytest.mark.asyncio
async def test_list_nodes_pagination_custom_limit_and_offset() -> None:
    """list_nodes honours explicit limit and offset parameters."""
    db = AsyncMock()
    db.execute.side_effect = [
        _scalars_result([_node(node_id="node-page2")]),
        _rows_result([("node-page2", 0)]),
    ]

    response = await list_nodes(
        limit=5,
        offset=10,
        current_user={"sub": "admin"},
        db=db,
    )

    assert len(response) == 1
    call_args = db.execute.call_args_list[0]
    compiled = str(call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
    assert "5" in compiled
    assert "10" in compiled
