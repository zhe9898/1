"""Add alert_rules and alerts tables

Revision ID: 0012_alerts
Revises: 0011_quotas
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = '0012_alerts'
down_revision = '0011_quotas'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('condition', sa.JSON(), nullable=False),
        sa.Column('action', sa.JSON(), nullable=False),
        sa.Column('severity', sa.String(32), nullable=False, server_default='warning'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alert_rules_tenant_id', 'alert_rules', ['tenant_id'])
    op.create_index('ix_alert_rules_tenant_enabled', 'alert_rules', ['tenant_id', 'enabled'])

    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('rule_id', sa.Integer(), nullable=False),
        sa.Column('rule_name', sa.String(128), nullable=False),
        sa.Column('severity', sa.String(32), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('notified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('triggered_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alerts_tenant_id', 'alerts', ['tenant_id'])
    op.create_index('ix_alerts_tenant_rule', 'alerts', ['tenant_id', 'rule_id'])
    op.create_index('ix_alerts_tenant_resolved', 'alerts', ['tenant_id', 'resolved_at'])
    op.create_index('ix_alerts_triggered_at', 'alerts', ['triggered_at'])


def downgrade() -> None:
    op.drop_index('ix_alerts_triggered_at', 'alerts')
    op.drop_index('ix_alerts_tenant_resolved', 'alerts')
    op.drop_index('ix_alerts_tenant_rule', 'alerts')
    op.drop_index('ix_alerts_tenant_id', 'alerts')
    op.drop_table('alerts')

    op.drop_index('ix_alert_rules_tenant_enabled', 'alert_rules')
    op.drop_index('ix_alert_rules_tenant_id', 'alert_rules')
    op.drop_table('alert_rules')
