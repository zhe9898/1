from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.jobs import JobCreateRequest, _get_job_by_idempotency_key, create_job


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    result.scalars.return_value = scalars
    result.scalar.return_value = value
    return result


def _row_result(*, global_count: int = 0, tenant_count: int = 0, connector_count: int = 0) -> MagicMock:
    result = MagicMock()
    result.one.return_value = MagicMock(
        global_count=global_count,
        tenant_count=tenant_count,
        connector_count=connector_count,
    )
    return result


def _render_sql(statement: object) -> str:
    return str(statement)


def _control_plane_migration_text() -> str:
    path = Path(__file__).resolve().parents[3] / "backend" / "alembic" / "versions" / "9f2c7a1d4e61_control_plane_schema_hardening.py"
    return path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_idempotency_lookup_scopes_by_tenant() -> None:
    db = AsyncMock()
    db.execute.return_value = _scalar_result(None)

    await _get_job_by_idempotency_key(db, "tenant-alpha", "invoke-1")

    stmt = db.execute.await_args.args[0]
    rendered = _render_sql(stmt)
    assert "jobs.tenant_id" in rendered
    assert "jobs.idempotency_key" in rendered


@pytest.mark.asyncio
async def test_create_job_allows_same_idempotency_key_in_other_tenant() -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    def add_side_effect(job: object) -> None:
        job.attempt = 0
        job.created_at = now
        job.updated_at = now

    db.add = MagicMock(side_effect=add_side_effect)

    existing_other_tenant = MagicMock()
    existing_other_tenant.tenant_id = "tenant-beta"

    def execute_side_effect(statement: object, *args: object, **kwargs: object) -> MagicMock:
        rendered = str(statement)
        if "zen70_global_leased_jobs_count" in rendered.lower() and "tenant_count" in rendered.lower():
            return _row_result()
        compiled = statement.compile()
        params = compiled.params
        if params.get("tenant_id_1") == "tenant-alpha" and params.get("idempotency_key_1") == "invoke-1":
            return _scalar_result(None)
        return _scalar_result(existing_other_tenant)

    db.execute.side_effect = execute_side_effect
    import backend.kernel.scheduling.scheduling_resilience as scheduling_resilience

    original_check_admission = scheduling_resilience.AdmissionController.check_admission
    scheduling_resilience.AdmissionController.check_admission = AsyncMock(return_value=(True, "", {}))

    try:
        response = await create_job(
            JobCreateRequest(
                kind="connector.invoke",
                connector_id="connector-1",
                payload={"connector_id": "connector-1", "action": "read", "parameters": {"hello": "world"}},
                lease_seconds=30,
                idempotency_key="invoke-1",
            ),
            current_user={"sub": "tester", "tenant_id": "tenant-alpha", "role": "admin"},
            db=db,
            redis=None,
        )
    finally:
        scheduling_resilience.AdmissionController.check_admission = original_check_admission

    assert response.job_id
    assert response.idempotency_key == "invoke-1"
    assert db.add.called is True


def test_control_plane_schema_migration_uses_tenant_scoped_uniqueness() -> None:
    rendered = _control_plane_migration_text()

    assert '"users_username_key"' in rendered
    assert '"ux_users_tenant_username"' in rendered
    assert '"jobs_idempotency_key_key"' in rendered or '"ux_jobs_idempotency_key"' in rendered
    assert '"ux_jobs_tenant_idempotency_key"' in rendered
    assert '"nodes_node_id_key"' in rendered
    assert '"ux_nodes_tenant_node_id"' in rendered
    assert '"connectors_connector_id_key"' in rendered
    assert '"ux_connectors_tenant_connector_id"' in rendered


def test_machine_endpoints_use_machine_tenant_db_dependency() -> None:
    # Route handlers now live in the modular jobs/ package split across routes/dispatch/lifecycle
    jobs_dispatch_source = Path(__file__).resolve().parents[3] / "backend" / "api" / "jobs" / "dispatch.py"
    jobs_lifecycle_source = Path(__file__).resolve().parents[3] / "backend" / "api" / "jobs" / "lifecycle.py"
    jobs_db_source = Path(__file__).resolve().parents[3] / "backend" / "api" / "jobs" / "database.py"
    nodes_source = Path(__file__).resolve().parents[3] / "backend" / "api" / "nodes.py"
    nodes_helpers_source = Path(__file__).resolve().parents[3] / "backend" / "api" / "nodes_helpers.py"

    jobs_dispatch_text = jobs_dispatch_source.read_text(encoding="utf-8")
    jobs_lifecycle_text = jobs_lifecycle_source.read_text(encoding="utf-8")
    jobs_db_text = jobs_db_source.read_text(encoding="utf-8")
    nodes_text = nodes_source.read_text(encoding="utf-8")
    nodes_helpers_text = nodes_helpers_source.read_text(encoding="utf-8")

    assert "db: AsyncSession = Depends(get_machine_tenant_db)" in jobs_dispatch_text
    assert "db: AsyncSession = Depends(get_machine_tenant_db)" in jobs_lifecycle_text
    assert "db: AsyncSession = Depends(get_machine_tenant_db)" in nodes_text
    # DB query pattern lives in the database/helper modules, not the routes layer
    assert "select(Job).where(Job.tenant_id == tenant_id, Job.job_id == job_id)" in jobs_db_text
    assert "select(Node).where(Node.tenant_id == tenant_id, Node.node_id == node_id)" in nodes_helpers_text
