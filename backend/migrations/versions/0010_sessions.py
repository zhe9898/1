"""Add sessions table

Revision ID: 0010_sessions
Revises: 0009_permissions
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa

revision = '0010_sessions'
down_revision = '0009_permissions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(64), nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('user_id', sa.String(128), nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('jti', sa.String(64), nullable=False),
        sa.Column('ip_address', sa.String(64), nullable=True),
        sa.Column('user_agent', sa.String(512), nullable=True),
        sa.Column('device_name', sa.String(128), nullable=True),
        sa.Column('auth_method', sa.String(32), nullable=False, server_default='password'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_by', sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', name='ux_sessions_session_id'),
        sa.UniqueConstraint('jti', name='ux_sessions_jti'),
    )
    op.create_index('ix_sessions_tenant_user', 'sessions', ['tenant_id', 'user_id'])
    op.create_index('ix_sessions_is_active', 'sessions', ['is_active'])
    op.create_index('ix_sessions_expires_at', 'sessions', ['expires_at'])
    op.create_index('ix_sessions_created_at', 'sessions', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_sessions_created_at', 'sessions')
    op.drop_index('ix_sessions_expires_at', 'sessions')
    op.drop_index('ix_sessions_is_active', 'sessions')
    op.drop_index('ix_sessions_tenant_user', 'sessions')
    op.drop_table('sessions')
