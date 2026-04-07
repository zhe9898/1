from __future__ import annotations

from pathlib import Path


DB_PATH = Path("backend/db.py")
MIGRATION_PATH = Path("backend/alembic/versions/9f2c7a1d4e61_control_plane_schema_hardening.py")
PUSH_MIGRATION_PATH = Path("backend/alembic/versions/c13c4b7d9a12_push_subscription_tenant_scope.py")


def test_db_runtime_no_longer_embeds_schema_hardening_ddl() -> None:
    text = DB_PATH.read_text(encoding="utf-8")

    assert "_SCHEMA_HARDENING_STATEMENTS" not in text
    assert "ALTER TABLE jobs" not in text
    assert "ALTER TABLE nodes" not in text
    assert "ALTER TABLE connectors" not in text
    assert "CREATE TABLE IF NOT EXISTS job_attempts" not in text
    assert "flow through Alembic migrations" in text


def test_control_plane_schema_hardening_lives_in_alembic() -> None:
    assert MIGRATION_PATH.exists()

    text = MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'revision: str = "9f2c7a1d4e61"' in text
    assert '_ensure_job_attempts_schema' in text
    assert '"ux_jobs_tenant_idempotency_key"' in text
    assert '"ux_nodes_tenant_node_id"' in text
    assert '"ux_connectors_tenant_connector_id"' in text


def test_push_subscription_tenant_scope_lives_in_alembic() -> None:
    assert PUSH_MIGRATION_PATH.exists()

    text = PUSH_MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'revision: str = "c13c4b7d9a12"' in text
    assert 'down_revision: Union[str, Sequence[str], None] = "9f2c7a1d4e61"' in text
    assert '"tenant_id"' in text
    assert '"ux_push_subscriptions_tenant_endpoint"' in text
