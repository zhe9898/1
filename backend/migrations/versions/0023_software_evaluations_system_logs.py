"""Add software_evaluations and system_logs tables

Revision ID: 0023_software_evaluations_system_logs
Revises: 0022_memory_facts
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_software_evaluations_system_logs"
down_revision = "0022_memory_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "evaluation_id", name="ux_software_evaluations_tenant_eval_id"),
    )
    op.create_index("ix_software_evaluations_tenant_id", "software_evaluations", ["tenant_id"])
    op.create_index("ix_software_evaluations_evaluation_id", "software_evaluations", ["evaluation_id"])
    op.create_index("ix_software_evaluations_software_id", "software_evaluations", ["software_id"])
    op.create_index("ix_software_evaluations_branch", "software_evaluations", ["branch"])
    op.create_index("ix_software_evaluations_category", "software_evaluations", ["category"])
    op.create_index("ix_software_evaluations_status", "software_evaluations", ["status"])

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
    op.create_index("ix_system_logs_level", "system_logs", ["level"])
    op.create_index("ix_system_logs_action", "system_logs", ["action"])


def downgrade() -> None:
    op.drop_index("ix_system_logs_action", "system_logs")
    op.drop_index("ix_system_logs_level", "system_logs")
    op.drop_table("system_logs")

    op.drop_index("ix_software_evaluations_status", "software_evaluations")
    op.drop_index("ix_software_evaluations_category", "software_evaluations")
    op.drop_index("ix_software_evaluations_branch", "software_evaluations")
    op.drop_index("ix_software_evaluations_software_id", "software_evaluations")
    op.drop_index("ix_software_evaluations_evaluation_id", "software_evaluations")
    op.drop_index("ix_software_evaluations_tenant_id", "software_evaluations")
    op.drop_table("software_evaluations")
