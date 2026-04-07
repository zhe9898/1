"""Add software_evaluations and system_logs tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.core.migration_schema_guard import SchemaGuard

revision = "0023_software_evaluations_system_logs"
down_revision = "0022_memory_facts"
branch_labels = None
depends_on = None


def _ensure_software_evaluations(guard: SchemaGuard) -> None:
    if not guard.has_table("software_evaluations"):
        op.create_table(
            "software_evaluations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("evaluation_id", sa.String(128), nullable=False),
            sa.Column("software_id", sa.String(128), nullable=False),
            sa.Column("branch", sa.String(128), nullable=False, server_default="main"),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(64), nullable=False, server_default="general"),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("evaluator", sa.String(128), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="submitted"),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "evaluation_id", name="ux_software_evaluations_tenant_eval_id"),
        )
        guard.refresh()
    else:
        for column in (
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("evaluation_id", sa.String(128), nullable=False),
            sa.Column("software_id", sa.String(128), nullable=False),
            sa.Column("branch", sa.String(128), nullable=False, server_default="main"),
            sa.Column("rating", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("category", sa.String(64), nullable=False, server_default="general"),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("evaluator", sa.String(128), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="submitted"),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        ):
            guard.add_column_if_missing("software_evaluations", column)

    guard.create_unique_constraint_if_missing(
        "software_evaluations",
        "ux_software_evaluations_tenant_eval_id",
        ["tenant_id", "evaluation_id"],
    )
    guard.create_index_if_missing("software_evaluations", "ix_software_evaluations_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("software_evaluations", "ix_software_evaluations_evaluation_id", ["evaluation_id"])
    guard.create_index_if_missing("software_evaluations", "ix_software_evaluations_software_id", ["software_id"])
    guard.create_index_if_missing("software_evaluations", "ix_software_evaluations_branch", ["branch"])
    guard.create_index_if_missing("software_evaluations", "ix_software_evaluations_category", ["category"])
    guard.create_index_if_missing("software_evaluations", "ix_software_evaluations_status", ["status"])


def upgrade() -> None:
    guard = SchemaGuard(op.get_bind())
    _ensure_software_evaluations(guard)

    if not guard.has_table("system_logs"):
        op.create_table(
            "system_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("level", sa.String(50), nullable=False),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("operator", sa.String(255), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        guard.refresh()

    guard.create_index_if_missing("system_logs", "ix_system_logs_level", ["level"])
    guard.create_index_if_missing("system_logs", "ix_system_logs_action", ["action"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("system_logs"):
        op.drop_index("ix_system_logs_action", "system_logs")
        op.drop_index("ix_system_logs_level", "system_logs")
        op.drop_table("system_logs")
    if inspector.has_table("software_evaluations"):
        op.drop_index("ix_software_evaluations_status", "software_evaluations")
        op.drop_index("ix_software_evaluations_category", "software_evaluations")
        op.drop_index("ix_software_evaluations_branch", "software_evaluations")
        op.drop_index("ix_software_evaluations_software_id", "software_evaluations")
        op.drop_index("ix_software_evaluations_evaluation_id", "software_evaluations")
        op.drop_index("ix_software_evaluations_tenant_id", "software_evaluations")
        op.drop_table("software_evaluations")
