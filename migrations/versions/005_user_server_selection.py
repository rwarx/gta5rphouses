"""per-user active server selection (chosen at /start)

Revision ID: 005
Revises: 004
Create Date: 2026-07-20

"""
from alembic import op
import sqlalchemy as sa


revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The single server a Telegram user is "browsing" — catalog commands and the
    # map view default to it. One row per user (user_id unique); re-selecting a
    # server upserts this row.
    op.create_table(
        'user_server_selection',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('server_sid', sa.String(8), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_user_server_selection_user'),
    )
    op.create_index(op.f('ix_user_server_selection_user_id'), 'user_server_selection', ['user_id'])


def downgrade() -> None:
    op.drop_table('user_server_selection')
