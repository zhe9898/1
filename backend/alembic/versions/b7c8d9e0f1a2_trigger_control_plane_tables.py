"""Trigger control-plane tables for unified ingress and delivery history.

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-01 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
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


def _create_unique_constraint_if_missing(table_name: str, constraint_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and not _has_unique_constraint(table_name, constraint_name):
        op.create_unique_constraint(constraint_name, table_name, columns)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_unique_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    if _has_table(table_name) and _has_unique_constraint(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="unique")


def _ensure_triggers_schema() -> None:
    if not _has_table("triggers"):
        op.create_table(
            "triggers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("trigger_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("kind", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("target", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("input_defaults", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("last_fired_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("last_delivery_status", sa.String(length=32), nullable=True),
            sa.Column("last_delivery_message", sa.String(length=255), nullable=True),
            sa.Column("last_delivery_id", sa.String(length=128), nullable=True),
            sa.Column("last_delivery_target_kind", sa.String(length=64), nullable=True),
            sa.Column("last_delivery_target_id", sa.String(length=128), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("updated_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    else:
        for column in (
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("trigger_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False, server_default="unnamed-trigger"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("kind", sa.String(length=64), nullable=False, server_default="manual"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("target", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("input_defaults", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("last_fired_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("last_delivery_status", sa.String(length=32), nullable=True),
            sa.Column("last_delivery_message", sa.String(length=255), nullable=True),
            sa.Column("last_delivery_id", sa.String(length=128), nullable=True),
            sa.Column("last_delivery_target_kind", sa.String(length=64), nullable=True),
            sa.Column("last_delivery_target_id", sa.String(length=128), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("updated_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        ):
            _add_column_if_missing("triggers", column)

    _create_index_if_missing("triggers", "ix_triggers_tenant_id", ["tenant_id"])
    _create_index_if_missing("triggers", "ix_triggers_trigger_id", ["trigger_id"])
    _create_index_if_missing("triggers", "ix_triggers_kind", ["kind"])
    _create_index_if_missing("triggers", "ix_triggers_status", ["status"])
    _create_index_if_missing("triggers", "ix_triggers_next_run_at", ["next_run_at"])
    _create_index_if_missing("triggers", "ix_triggers_tenant_status", ["tenant_id", "status"])
    _create_index_if_missing("triggers", "ix_triggers_tenant_kind", ["tenant_id", "kind"])
    _create_unique_constraint_if_missing("triggers", "ux_triggers_tenant_trigger_id", ["tenant_id", "trigger_id"])


def _ensure_trigger_deliveries_schema() -> None:
    if not _has_table("trigger_deliveries"):
        op.create_table(
            "trigger_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("delivery_id", sa.String(length=128), nullable=False),
            sa.Column("trigger_id", sa.String(length=128), nullable=False),
            sa.Column("trigger_kind", sa.String(length=64), nullable=False),
            sa.Column("source_kind", sa.String(length=64), nullable=False, server_default="manual"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="dispatching"),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("actor", sa.String(length=128), nullable=True),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("input_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("context", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("target_kind", sa.String(length=64), nullable=True),
            sa.Column("target_id", sa.String(length=128), nullable=True),
            sa.Column("target_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("fired_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("delivered_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    else:
        for column in (
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("delivery_id", sa.String(length=128), nullable=False),
            sa.Column("trigger_id", sa.String(length=128), nullable=False),
            sa.Column("trigger_kind", sa.String(length=64), nullable=False, server_default="manual"),
            sa.Column("source_kind", sa.String(length=64), nullable=False, server_default="manual"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="dispatching"),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("actor", sa.String(length=128), nullable=True),
            sa.Column("reason", sa.String(length=255), nullable=True),
            sa.Column("input_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("context", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("target_kind", sa.String(length=64), nullable=True),
            sa.Column("target_id", sa.String(length=128), nullable=True),
            sa.Column("target_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("fired_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("delivered_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        ):
            _add_column_if_missing("trigger_deliveries", column)

    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_tenant_id", ["tenant_id"])
    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_delivery_id", ["delivery_id"])
    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_trigger_id", ["trigger_id"])
    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_trigger_kind", ["trigger_kind"])
    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_source_kind", ["source_kind"])
    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_status", ["status"])
    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_fired_at", ["fired_at"])
    _create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_tenant_trigger", ["tenant_id", "trigger_id"])
    _create_unique_constraint_if_missing("trigger_deliveries", "uq_trigger_deliveries_delivery_id", ["delivery_id"])
    _create_index_if_missing(
        "trigger_deliveries",
        "ux_trigger_deliveries_tenant_trigger_idempotency",
        ["tenant_id", "trigger_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def upgrade() -> None:
    _ensure_triggers_schema()
    _ensure_trigger_deliveries_schema()


def downgrade() -> None:
    _drop_index_if_exists("trigger_deliveries", "ux_trigger_deliveries_tenant_trigger_idempotency")
    _drop_unique_constraint_if_exists("trigger_deliveries", "uq_trigger_deliveries_delivery_id")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_tenant_trigger")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_fired_at")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_status")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_source_kind")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_trigger_kind")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_trigger_id")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_delivery_id")
    _drop_index_if_exists("trigger_deliveries", "ix_trigger_deliveries_tenant_id")
    if _has_table("trigger_deliveries"):
        op.drop_table("trigger_deliveries")

    _drop_unique_constraint_if_exists("triggers", "ux_triggers_tenant_trigger_id")
    _drop_index_if_exists("triggers", "ix_triggers_tenant_kind")
    _drop_index_if_exists("triggers", "ix_triggers_tenant_status")
    _drop_index_if_exists("triggers", "ix_triggers_next_run_at")
    _drop_index_if_exists("triggers", "ix_triggers_status")
    _drop_index_if_exists("triggers", "ix_triggers_kind")
    _drop_index_if_exists("triggers", "ix_triggers_trigger_id")
    _drop_index_if_exists("triggers", "ix_triggers_tenant_id")
    if _has_table("triggers"):
        op.drop_table("triggers")
