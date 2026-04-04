"""Control-plane schema hardening for tenant-aware nodes/jobs/connectors

Revision ID: 9f2c7a1d4e61
Revises: 5e3a7d2c9f40
Create Date: 2026-03-28 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9f2c7a1d4e61"
down_revision: Union[str, Sequence[str], None] = "5e3a7d2c9f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    return any(constraint["name"] == constraint_name for constraint in _inspector().get_unique_constraints(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column[object]) -> None:
    if _has_table(table_name) and not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_unique_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    if _has_table(table_name) and _has_unique_constraint(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="unique")


def _create_index_if_missing(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
    postgresql_where: sa.TextClause | None = None,
) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(
            index_name,
            table_name,
            columns,
            unique=unique,
            postgresql_where=postgresql_where,
        )


def _create_unique_constraint_if_missing(
    table_name: str,
    constraint_name: str,
    columns: list[str],
) -> None:
    if _has_table(table_name) and not _has_unique_constraint(table_name, constraint_name):
        op.create_unique_constraint(constraint_name, table_name, columns)


def _backfill_default_tenant(table_name: str) -> None:
    if _has_table(table_name) and _has_column(table_name, "tenant_id"):
        op.execute(sa.text(f"UPDATE {table_name} SET tenant_id = 'default' WHERE tenant_id IS NULL"))


def _ensure_jobs_schema() -> None:
    for column in (
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("target_os", sa.String(length=64), nullable=True),
        sa.Column("target_arch", sa.String(length=64), nullable=True),
        sa.Column("target_executor", sa.String(length=64), nullable=True),
        sa.Column("required_capabilities", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("target_zone", sa.String(length=128), nullable=True),
        sa.Column("required_cpu_cores", sa.Integer(), nullable=True),
        sa.Column("required_memory_mb", sa.Integer(), nullable=True),
        sa.Column("required_gpu_vram_mb", sa.Integer(), nullable=True),
        sa.Column("required_storage_mb", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_duration_s", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="console"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
    ):
        _add_column_if_missing("jobs", column)

    _backfill_default_tenant("jobs")
    _drop_unique_constraint_if_exists("jobs", "jobs_idempotency_key_key")
    _drop_index_if_exists("jobs", "ux_jobs_idempotency_key")
    _create_index_if_missing("jobs", "ix_jobs_tenant_id", ["tenant_id"])
    _create_index_if_missing(
        "jobs",
        "ux_jobs_tenant_idempotency_key",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def _ensure_nodes_schema() -> None:
    for column in (
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("zone", sa.String(length=128), nullable=True),
        sa.Column("executor", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("os", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("arch", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("protocol_version", sa.String(length=32), nullable=False, server_default="runner.v1"),
        sa.Column("lease_version", sa.String(length=32), nullable=False, server_default="job-lease.v1"),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cpu_cores", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gpu_vram_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("drain_status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("health_reason", sa.String(length=255), nullable=True),
        sa.Column("auth_token_hash", sa.String(length=255), nullable=True),
        sa.Column("auth_token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enrollment_status", sa.String(length=32), nullable=False, server_default="pending"),
    ):
        _add_column_if_missing("nodes", column)

    _backfill_default_tenant("nodes")
    _drop_unique_constraint_if_exists("nodes", "nodes_node_id_key")
    _create_index_if_missing("nodes", "ix_nodes_tenant_id", ["tenant_id"])
    _create_index_if_missing("nodes", "ix_nodes_enrollment_status", ["enrollment_status"])
    _create_index_if_missing("nodes", "ix_nodes_drain_status", ["drain_status"])
    _create_unique_constraint_if_missing("nodes", "ux_nodes_tenant_node_id", ["tenant_id", "node_id"])


def _ensure_connectors_schema() -> None:
    for column in (
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_test_message", sa.String(length=255), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("last_invoke_status", sa.String(length=32), nullable=True),
        sa.Column("last_invoke_message", sa.String(length=255), nullable=True),
        sa.Column("last_invoke_job_id", sa.String(length=128), nullable=True),
        sa.Column("last_invoke_at", sa.DateTime(timezone=False), nullable=True),
    ):
        _add_column_if_missing("connectors", column)

    _backfill_default_tenant("connectors")
    _drop_unique_constraint_if_exists("connectors", "connectors_connector_id_key")
    _create_index_if_missing("connectors", "ix_connectors_tenant_id", ["tenant_id"])
    _create_unique_constraint_if_missing(
        "connectors",
        "ux_connectors_tenant_connector_id",
        ["tenant_id", "connector_id"],
    )


def _ensure_job_logs_schema() -> None:
    _add_column_if_missing(
        "job_logs",
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
    )
    _backfill_default_tenant("job_logs")
    _create_index_if_missing("job_logs", "ix_job_logs_tenant_id", ["tenant_id"])


def _ensure_job_attempts_schema() -> None:
    if not _has_table("job_attempts"):
        op.create_table(
            "job_attempts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("attempt_id", sa.String(length=128), nullable=False),
            sa.Column("job_id", sa.String(length=128), nullable=False),
            sa.Column("node_id", sa.String(length=128), nullable=False),
            sa.Column("lease_token", sa.String(length=64), nullable=False),
            sa.Column("attempt_no", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="leased"),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_summary", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=False),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("started_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=False),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
    else:
        for column in (
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("attempt_id", sa.String(length=128), nullable=False),
            sa.Column("job_id", sa.String(length=128), nullable=False),
            sa.Column("node_id", sa.String(length=128), nullable=False),
            sa.Column("lease_token", sa.String(length=64), nullable=False),
            sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="leased"),
            sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_summary", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=False),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("started_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=False),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        ):
            _add_column_if_missing("job_attempts", column)

    _backfill_default_tenant("job_attempts")
    _create_index_if_missing("job_attempts", "ix_job_attempts_tenant_id", ["tenant_id"])
    _create_index_if_missing("job_attempts", "ix_job_attempts_job_id", ["job_id"])
    _create_index_if_missing("job_attempts", "ix_job_attempts_node_id", ["node_id"])
    _create_index_if_missing("job_attempts", "ix_job_attempts_status", ["status"])
    _create_unique_constraint_if_missing("job_attempts", "ux_job_attempts_attempt_id", ["attempt_id"])
    _create_unique_constraint_if_missing(
        "job_attempts",
        "ux_job_attempts_lease",
        ["job_id", "attempt_no", "lease_token"],
    )


def _ensure_users_tenant_scoped_username() -> None:
    _drop_unique_constraint_if_exists("users", "users_username_key")
    _create_unique_constraint_if_missing("users", "ux_users_tenant_username", ["tenant_id", "username"])


def upgrade() -> None:
    _ensure_jobs_schema()
    _ensure_nodes_schema()
    _ensure_connectors_schema()
    _ensure_job_logs_schema()
    _ensure_job_attempts_schema()
    _ensure_users_tenant_scoped_username()


def downgrade() -> None:
    _drop_unique_constraint_if_exists("job_attempts", "ux_job_attempts_lease")
    _drop_unique_constraint_if_exists("job_attempts", "ux_job_attempts_attempt_id")
    _drop_index_if_exists("job_attempts", "ix_job_attempts_status")
    _drop_index_if_exists("job_attempts", "ix_job_attempts_node_id")
    _drop_index_if_exists("job_attempts", "ix_job_attempts_job_id")
    _drop_index_if_exists("job_attempts", "ix_job_attempts_tenant_id")
    if _has_table("job_attempts"):
        op.drop_table("job_attempts")

    _drop_index_if_exists("job_logs", "ix_job_logs_tenant_id")

    _drop_unique_constraint_if_exists("connectors", "ux_connectors_tenant_connector_id")
    _drop_index_if_exists("connectors", "ix_connectors_tenant_id")

    _drop_unique_constraint_if_exists("nodes", "ux_nodes_tenant_node_id")
    _drop_index_if_exists("nodes", "ix_nodes_drain_status")
    _drop_index_if_exists("nodes", "ix_nodes_enrollment_status")
    _drop_index_if_exists("nodes", "ix_nodes_tenant_id")

    _drop_index_if_exists("jobs", "ux_jobs_tenant_idempotency_key")
    _drop_index_if_exists("jobs", "ix_jobs_tenant_id")

    _drop_unique_constraint_if_exists("users", "ux_users_tenant_username")
