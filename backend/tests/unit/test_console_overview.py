from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.console import get_console_diagnostics, get_console_overview
from backend.models.connector import Connector
from backend.models.job import Job
from backend.models.node import Node


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _render_sql(statement: object) -> str:
    return str(statement)


def _result_with(items: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = items
    result.scalars.return_value = scalars
    return result


def _node(**overrides: object) -> Node:
    now = _utcnow()
    node = Node(
        tenant_id="default",
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
        enrollment_status="approved",
        status="online",
        capabilities=["connector.invoke"],
        metadata_json={},
        registered_at=now,
        last_seen_at=now,
        updated_at=now,
    )
    for key, value in overrides.items():
        setattr(node, key, value)
    return node


def _job(**overrides: object) -> Job:
    now = _utcnow()
    job = Job(
        tenant_id="default",
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


def _connector(**overrides: object) -> Connector:
    now = _utcnow()
    connector = Connector(
        tenant_id="default",
        connector_id="conn-a",
        name="Connector A",
        kind="http",
        status="healthy",
        endpoint=None,
        profile="manual",
        config={},
        updated_at=now,
        created_at=now,
    )
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


@pytest.mark.asyncio
async def test_console_overview_aggregates_attention_buckets() -> None:
    now = _utcnow()
    db = AsyncMock()
    db.execute.side_effect = [
        _result_with(
            [
                _node(node_id="node-active"),
                _node(node_id="node-pending", enrollment_status="pending"),
                _node(node_id="node-offline", status="offline"),
                _node(node_id="node-stale", last_seen_at=now - datetime.timedelta(minutes=5)),
            ]
        ),
        _result_with(
            [
                _job(job_id="job-high", priority=90, status="pending"),
                _job(job_id="job-running", status="leased", leased_until=now + datetime.timedelta(seconds=20)),
                _job(job_id="job-stale", status="leased", leased_until=now - datetime.timedelta(seconds=20)),
                _job(job_id="job-failed", status="failed"),
                _job(job_id="job-cancelled", status="cancelled"),
                _job(job_id="job-completed", status="completed"),
            ]
        ),
        _result_with(
            [
                _connector(connector_id="conn-healthy", status="healthy"),
                _connector(connector_id="conn-configured", status="configured"),
                _connector(connector_id="conn-error", status="error"),
            ]
        ),
    ]

    response = await get_console_overview(current_user={"sub": "admin", "tenant_id": "default"}, db=db)

    assert response.nodes.active == 1
    assert response.nodes.pending == 1
    assert response.nodes.offline == 1
    assert response.nodes.degraded == 1
    assert response.jobs.pending == 1
    assert response.jobs.running == 1
    assert response.jobs.stale == 1
    assert response.jobs.failed == 1
    assert response.jobs.cancelled == 1
    assert response.jobs.completed == 1
    assert response.jobs.high_priority_backlog == 1
    assert response.connectors.active == 1
    assert response.connectors.pending == 1
    assert response.connectors.failed == 1
    assert response.summary_cards[0].key == "nodes"
    assert response.summary_cards[0].tone_view.key == "info"
    assert response.summary_cards[0].route is not None
    assert response.summary_cards[0].route.route_path == "/nodes"
    assert response.summary_cards[1].route.query["status"] == "pending"
    assert [item.title for item in response.attention][:3] == [
        "Node health requires attention",
        "High-priority backlog detected",
        "Leases expired without completion",
    ]
    assert response.attention[0].severity_view.key == "critical"
    assert response.attention[0].severity_view.tone == "danger"
    assert response.attention[0].route.query["attention"] == "attention"
    node_stmt = db.execute.await_args_list[0].args[0]
    job_stmt = db.execute.await_args_list[1].args[0]
    connector_stmt = db.execute.await_args_list[2].args[0]
    assert "nodes.tenant_id" in _render_sql(node_stmt)
    assert "jobs.tenant_id" in _render_sql(job_stmt)
    assert "connectors.tenant_id" in _render_sql(connector_stmt)


@pytest.mark.asyncio
async def test_console_diagnostics_reports_stale_and_unschedulable_work() -> None:
    now = _utcnow()
    db = AsyncMock()
    db.execute.side_effect = [
        _result_with(
            [
                _node(node_id="node-active", max_concurrency=2),
                _node(node_id="node-draining", drain_status="draining"),
            ]
        ),
        _result_with(
            [
                _job(
                    job_id="job-stale",
                    status="leased",
                    node_id="node-active",
                    attempt=2,
                    leased_until=now - datetime.timedelta(seconds=15),
                    priority=70,
                    source="runner",
                ),
                _job(
                    job_id="job-blocked",
                    status="pending",
                    target_os="windows",
                    required_capabilities=["gpu"],
                    target_zone="lab-b",
                    priority=95,
                ),
            ]
        ),
        MagicMock(all=MagicMock(return_value=[("node-active", "completed"), ("node-draining", "failed")])),
        _result_with(
            [
                _connector(connector_id="conn-stale", status="configured", last_test_message="waiting for first test"),
                _connector(connector_id="conn-bad", status="error", last_test_status="error", last_test_message="auth failed"),
            ]
        ),
    ]

    response = await get_console_diagnostics(current_user={"sub": "admin", "tenant_id": "default"}, db=db)

    assert response.stale_jobs[0].job_id == "job-stale"
    assert response.stale_jobs[0].lease_state == "stale"
    assert response.stale_jobs[0].lease_state_view.key == "stale"
    assert response.stale_jobs[0].route.query["job_id"] == "job-stale"
    assert {action.key for action in response.stale_jobs[0].actions} == {"cancel", "retry", "explain"}
    assert response.unschedulable_jobs[0].job_id == "job-blocked"
    assert "os=windows" in response.unschedulable_jobs[0].selectors
    assert response.unschedulable_jobs[0].route.query["job_id"] == "job-blocked"
    assert any(action.key == "explain" for action in response.unschedulable_jobs[0].actions)
    assert any(segment.key == "lab-b" and segment.count == 1 for segment in response.backlog_by_zone)
    assert any(segment.key == "gpu" and segment.count == 1 for segment in response.backlog_by_capability)
    assert any(segment.key == "*" and segment.count == 1 for segment in response.backlog_by_executor)
    assert response.node_health[0].route.route_path == "/nodes"
    assert response.node_health[0].status_view.key == "online"
    assert response.node_health[0].executor == "go-native"
    assert any(action.key == "drain" for action in response.node_health[0].actions)
    assert {item.connector_id for item in response.connector_health} == {"conn-stale", "conn-bad"}
    assert any(item.connector_id == "conn-bad" for item in response.connector_health)
    assert any(item.status_view.key == "error" for item in response.connector_health)
    assert any(any(action.key == "test" for action in item.actions) for item in response.connector_health)
    assert response.backlog_by_zone[0].route.route_path == "/jobs"
    node_stmt = db.execute.await_args_list[0].args[0]
    job_stmt = db.execute.await_args_list[1].args[0]
    attempt_stmt = db.execute.await_args_list[2].args[0]
    connector_stmt = db.execute.await_args_list[3].args[0]
    assert "nodes.tenant_id" in _render_sql(node_stmt)
    assert "jobs.tenant_id" in _render_sql(job_stmt)
    assert "job_attempts.tenant_id" in _render_sql(attempt_stmt)
    assert "connectors.tenant_id" in _render_sql(connector_stmt)
