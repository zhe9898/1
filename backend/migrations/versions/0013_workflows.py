"""Add workflows and workflow_steps tables

Revision ID: 0013_workflows
Revises: 0012_alerts
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = '0013_workflows'
down_revision = '0012_alerts'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'workflows',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('workflow_id', sa.String(128), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('context', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_by', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'workflow_id', name='ux_workflows_tenant_id'),
    )
    op.create_index('ix_workflows_tenant_id', 'workflows', ['tenant_id'])
    op.create_index('ix_workflows_tenant_status', 'workflows', ['tenant_id', 'status'])
    op.create_index('ix_workflows_created_at', 'workflows', ['created_at'])

    op.create_table(
        'workflow_steps',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('workflow_id_fk', sa.Integer(), nullable=False),
        sa.Column('step_id', sa.String(64), nullable=False),
        sa.Column('job_id', sa.String(128), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='waiting'),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workflow_id_fk', 'step_id', name='ux_workflow_steps_id'),
    )
    op.create_index('ix_workflow_steps_workflow_status', 'workflow_steps', ['workflow_id_fk', 'status'])
    op.create_index('ix_workflow_steps_job_id', 'workflow_steps', ['job_id'])


def downgrade() -> None:
    op.drop_index('ix_workflow_steps_job_id', 'workflow_steps')
    op.drop_index('ix_workflow_steps_workflow_status', 'workflow_steps')
    op.drop_table('workflow_steps')

    op.drop_index('ix_workflows_created_at', 'workflows')
    op.drop_index('ix_workflows_tenant_status', 'workflows')
    op.drop_index('ix_workflows_tenant_id', 'workflows')
    op.drop_table('workflows')
