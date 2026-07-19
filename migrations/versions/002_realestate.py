"""realestate catalog source: objects + events

Revision ID: 002
Revises: 001
Create Date: 2026-07-19

"""
from alembic import op
import sqlalchemy as sa


revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Current known state of occupied objects from the /realestate catalog.
    op.create_table(
        'realestate_objects',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('object_key', sa.String(120), nullable=False),
        sa.Column('server_sid', sa.String(8), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('unit_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('price', sa.Integer(), nullable=True),
        sa.Column('class_name', sa.String(100), nullable=True),
        sa.Column('owner_name', sa.String(255), nullable=True),
        sa.Column('vehicle_count', sa.Integer(), nullable=True),
        sa.Column('building_name', sa.String(255), nullable=True),
        sa.Column('image', sa.String(500), nullable=True),
        sa.Column('is_occupied', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('object_key'),
    )
    op.create_index(op.f('ix_realestate_objects_object_key'), 'realestate_objects', ['object_key'], unique=True)
    op.create_index(op.f('ix_realestate_objects_server_sid'), 'realestate_objects', ['server_sid'])
    op.create_index(op.f('ix_realestate_objects_is_occupied'), 'realestate_objects', ['is_occupied'])
    op.create_index('idx_realestate_server_occupied', 'realestate_objects', ['server_sid', 'is_occupied'])

    # Detected transitions (freed / occupied / owner_changed).
    op.create_table(
        'realestate_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('object_key', sa.String(120), nullable=False),
        sa.Column('server_sid', sa.String(8), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('event_type', sa.String(30), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('price', sa.Integer(), nullable=True),
        sa.Column('class_name', sa.String(100), nullable=True),
        sa.Column('building_name', sa.String(255), nullable=True),
        sa.Column('old_owner', sa.String(255), nullable=True),
        sa.Column('new_owner', sa.String(255), nullable=True),
        sa.Column('detected_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('notified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_realestate_events_object_key'), 'realestate_events', ['object_key'])
    op.create_index(op.f('ix_realestate_events_server_sid'), 'realestate_events', ['server_sid'])
    op.create_index(op.f('ix_realestate_events_detected_at'), 'realestate_events', ['detected_at'])
    op.create_index(op.f('ix_realestate_events_notified'), 'realestate_events', ['notified'])
    op.create_index('idx_realestate_event_notified', 'realestate_events', ['notified', 'detected_at'])


def downgrade() -> None:
    op.drop_table('realestate_events')
    op.drop_table('realestate_objects')
