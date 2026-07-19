"""realestate: owner history + building aggregates

Revision ID: 003
Revises: 002
Create Date: 2026-07-19

"""
from alembic import op
import sqlalchemy as sa


revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Append-only owner-nickname timeline for each catalog object.
    op.create_table(
        'realestate_owner_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('object_key', sa.String(120), nullable=False),
        sa.Column('server_sid', sa.String(8), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('owner_name', sa.String(255), nullable=True),
        sa.Column('previous_owner', sa.String(255), nullable=True),
        sa.Column('during_payday', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_realestate_owner_history_object_key'), 'realestate_owner_history', ['object_key'])
    op.create_index(op.f('ix_realestate_owner_history_server_sid'), 'realestate_owner_history', ['server_sid'])
    op.create_index(op.f('ix_realestate_owner_history_recorded_at'), 'realestate_owner_history', ['recorded_at'])
    op.create_index('idx_owner_history_key_time', 'realestate_owner_history', ['object_key', 'recorded_at'])

    # Per-building aggregate rollup (free/total counts) for catalog listings.
    op.create_table(
        'realestate_buildings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('building_key', sa.String(120), nullable=False),
        sa.Column('server_sid', sa.String(8), nullable=False),
        sa.Column('building_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('apartments_count', sa.Integer(), nullable=True),
        sa.Column('free_count', sa.Integer(), nullable=True),
        sa.Column('image', sa.String(500), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('building_key'),
    )
    op.create_index(op.f('ix_realestate_buildings_building_key'), 'realestate_buildings', ['building_key'], unique=True)
    op.create_index(op.f('ix_realestate_buildings_server_sid'), 'realestate_buildings', ['server_sid'])
    op.create_index('idx_building_server', 'realestate_buildings', ['server_sid'])


def downgrade() -> None:
    op.drop_table('realestate_buildings')
    op.drop_table('realestate_owner_history')
