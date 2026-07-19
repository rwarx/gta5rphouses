"""realestate: per-user notification subscriptions

Revision ID: 004
Revises: 003
Create Date: 2026-07-19

"""
from alembic import op
import sqlalchemy as sa


revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A Telegram user's subscription to freed-object alerts for one server.
    op.create_table(
        'realestate_subscriptions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('server_sid', sa.String(8), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False, server_default='any'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'server_sid', name='uq_subscription_user_server'),
    )
    op.create_index(op.f('ix_realestate_subscriptions_user_id'), 'realestate_subscriptions', ['user_id'])
    op.create_index(op.f('ix_realestate_subscriptions_server_sid'), 'realestate_subscriptions', ['server_sid'])
    op.create_index('idx_subscription_server', 'realestate_subscriptions', ['server_sid'])


def downgrade() -> None:
    op.drop_table('realestate_subscriptions')
