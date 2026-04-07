"""Final dual-chain reconciliation fence for overlapping control-plane tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from backend.core.migration_schema_guard import SchemaGuard

revision = "0026_dual_chain_reconciliation"
down_revision = "0025_webauthn_credential_transports"
branch_labels = None
depends_on = None


def _ensure_jobs(guard: SchemaGuard) -> None:
    guard.add_column_if_missing("jobs", sa.Column("failure_category", sa.String(32), nullable=True))
    guard.add_column_if_missing("jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    guard.add_column_if_missing("jobs", sa.Column("preferred_device_profile", sa.String(64), nullable=True))
    guard.create_index_if_missing("jobs", "idx_jobs_failure_category", ["failure_category"])


def _ensure_job_attempts(guard: SchemaGuard) -> None:
    guard.add_column_if_missing("job_attempts", sa.Column("failure_category", sa.String(32), nullable=True))
    guard.add_column_if_missing("job_attempts", sa.Column("scheduling_decision_id", sa.Integer(), nullable=True))
    guard.create_index_if_missing("job_attempts", "idx_job_attempts_failure_category", ["failure_category"])
    guard.create_index_if_missing("job_attempts", "ix_job_attempts_scheduling_decision_id", ["scheduling_decision_id"])
    guard.create_unique_constraint_if_missing("job_attempts", "ux_job_attempts_attempt_id", ["attempt_id"])
    guard.create_unique_constraint_if_missing("job_attempts", "ux_job_attempts_lease", ["job_id", "attempt_no", "lease_token"])


def _ensure_nodes(guard: SchemaGuard) -> None:
    guard.add_column_if_missing("nodes", sa.Column("drain_until", sa.DateTime(), nullable=True))


def _ensure_users(guard: SchemaGuard) -> None:
    guard.add_column_if_missing("users", sa.Column("status", sa.String(32), nullable=False, server_default="active"))
    guard.add_column_if_missing("users", sa.Column("suspended_at", sa.DateTime(), nullable=True))
    guard.add_column_if_missing("users", sa.Column("suspended_by", sa.String(128), nullable=True))
    guard.add_column_if_missing("users", sa.Column("suspended_reason", sa.String(255), nullable=True))
    guard.add_column_if_missing("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    guard.create_index_if_missing("users", "ix_users_status", ["status"])
    guard.create_unique_constraint_if_missing("users", "ux_users_tenant_username", ["tenant_id", "username"])


def _ensure_webauthn_credentials(guard: SchemaGuard) -> None:
    guard.add_column_if_missing("webauthn_credentials", sa.Column("transports", sa.JSON(), nullable=True))


def _ensure_tenants(guard: SchemaGuard) -> None:
    guard.add_column_if_missing("tenants", sa.Column("display_name", sa.String(128), nullable=False, server_default="Home"))
    guard.add_column_if_missing("tenants", sa.Column("plan", sa.String(32), nullable=False, server_default="home"))
    guard.add_column_if_missing("tenants", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    guard.add_column_if_missing("tenants", sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    guard.add_column_if_missing("tenants", sa.Column("metadata_json", JSONB(), nullable=True))
    guard.create_index_if_missing("tenants", "ix_tenants_is_active", ["is_active"])
    op.execute(sa.text("""
            INSERT INTO tenants (tenant_id, display_name, plan, is_active, created_at)
            VALUES ('default', 'Default Home', 'home', true, NOW())
            ON CONFLICT (tenant_id) DO NOTHING
            """))


def _ensure_connectors(guard: SchemaGuard) -> None:
    guard.create_unique_constraint_if_missing("connectors", "ux_connectors_tenant_connector_id", ["tenant_id", "connector_id"])
    guard.create_index_if_missing("connectors", "ix_connectors_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("connectors", "ix_connectors_connector_id", ["connector_id"])
    guard.create_index_if_missing("connectors", "ix_connectors_kind", ["kind"])
    guard.create_index_if_missing("connectors", "ix_connectors_status", ["status"])


def _ensure_job_logs(guard: SchemaGuard) -> None:
    guard.create_index_if_missing("job_logs", "ix_job_logs_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("job_logs", "ix_job_logs_job_id", ["job_id"])
    guard.create_foreign_key_if_missing(
        "job_logs",
        "fk_job_logs_job_id_jobs",
        "jobs",
        ["job_id"],
        ["job_id"],
        ondelete="CASCADE",
    )


def _ensure_scheduling(guard: SchemaGuard) -> None:
    guard.add_column_if_missing(
        "tenant_scheduling_policies",
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
    )
    guard.create_unique_constraint_if_missing(
        "tenant_scheduling_policies",
        "ux_tenant_scheduling_policies_tenant_id",
        ["tenant_id"],
    )
    guard.create_index_if_missing("tenant_scheduling_policies", "ix_tenant_scheduling_policies_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("scheduling_decisions", "ix_scheduling_decisions_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("scheduling_decisions", "ix_scheduling_decisions_node_id", ["node_id"])
    guard.create_index_if_missing("scheduling_decisions", "ix_scheduling_decisions_cycle_ts", ["cycle_ts"])


def _ensure_triggers(guard: SchemaGuard) -> None:
    guard.create_unique_constraint_if_missing("triggers", "ux_triggers_tenant_trigger_id", ["tenant_id", "trigger_id"])
    guard.create_index_if_missing("triggers", "ix_triggers_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("triggers", "ix_triggers_trigger_id", ["trigger_id"])
    guard.create_index_if_missing("triggers", "ix_triggers_tenant_status", ["tenant_id", "status"])
    guard.create_index_if_missing("triggers", "ix_triggers_tenant_kind", ["tenant_id", "kind"])
    guard.create_index_if_missing("triggers", "ix_triggers_next_run_at", ["next_run_at"])
    guard.create_index_if_missing("triggers", "ix_triggers_created_at", ["created_at"])

    guard.create_unique_constraint_if_missing(
        "trigger_deliveries",
        "ux_trigger_deliveries_delivery_id",
        ["delivery_id"],
    )
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_delivery_id", ["delivery_id"])
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_trigger_id", ["trigger_id"])
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_trigger_kind", ["trigger_kind"])
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_status", ["status"])
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_fired_at", ["fired_at"])
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_tenant_trigger", ["tenant_id", "trigger_id"])
    guard.create_index_if_missing("trigger_deliveries", "ix_trigger_deliveries_tenant_status", ["tenant_id", "status"])
    guard.create_index_if_missing(
        "trigger_deliveries",
        "ux_trigger_deliveries_tenant_trigger_idempotency",
        ["tenant_id", "trigger_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def _ensure_memory_facts(guard: SchemaGuard) -> None:
    if not guard.has_column("memory_facts", "text"):
        op.add_column("memory_facts", sa.Column("text", sa.Text(), nullable=False, server_default=""))
        guard.refresh()
    if guard.has_column("memory_facts", "fact_text"):
        op.execute(sa.text("""
                UPDATE memory_facts
                SET text = fact_text
                WHERE (text IS NULL OR text = '')
                  AND COALESCE(fact_text, '') <> ''
                """))
        op.alter_column("memory_facts", "text", server_default=None)
        guard.refresh()
    guard.add_column_if_missing("memory_facts", sa.Column("confidence", sa.Float(), nullable=True, server_default="0.0"))
    guard.add_column_if_missing("memory_facts", sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.false()))
    guard.add_column_if_missing("memory_facts", sa.Column("superseded_by", UUID(as_uuid=True), nullable=True))
    guard.add_column_if_missing("memory_facts", sa.Column("vec384", ARRAY(sa.Float()), nullable=True))
    guard.add_column_if_missing("memory_facts", sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    guard.create_index_if_missing("memory_facts", "ix_memory_facts_tenant_id", ["tenant_id"])
    guard.create_index_if_missing("memory_facts", "ix_memory_facts_user_sub", ["user_sub"])
    guard.create_index_if_missing("memory_facts", "ix_memory_facts_created_at", ["created_at"])


def _ensure_software_evaluations(guard: SchemaGuard) -> None:
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
    _ensure_jobs(guard)
    _ensure_job_attempts(guard)
    _ensure_nodes(guard)
    _ensure_users(guard)
    _ensure_webauthn_credentials(guard)
    _ensure_tenants(guard)
    _ensure_connectors(guard)
    _ensure_job_logs(guard)
    _ensure_scheduling(guard)
    _ensure_triggers(guard)
    _ensure_memory_facts(guard)
    _ensure_software_evaluations(guard)


def downgrade() -> None:
    """This migration is intentionally irreversible."""
