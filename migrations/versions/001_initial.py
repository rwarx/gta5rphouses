"""Initial database schema

Revision ID: 001
Revises: 
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Apartments table
    op.create_table(
        'apartments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('apartment_id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('address', sa.String(255), nullable=True),
        sa.Column('total_apartments', sa.Integer(), nullable=True),
        sa.Column('free_apartments', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('occupied_apartments', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('wiki_url', sa.String(500), nullable=True),
        sa.Column('last_updated', sa.DateTime(timezone=True), nullable=True),
        sa.Column('raw_data', postgresql.JSON(), nullable=True),
        sa.Column('coordinates', postgresql.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('apartment_id'),
    )
    op.create_index('idx_apartment_free', 'apartments', ['free_apartments'])
    op.create_index('idx_apartment_active', 'apartments', ['is_active'])
    op.create_index('idx_apartment_id', 'apartments', ['apartment_id'])

    # Apartment types table
    op.create_table(
        'apartment_types',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('apartment_id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.String(100), nullable=False),
        sa.Column('total', sa.Integer(), nullable=True),
        sa.Column('free', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('occupied', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['apartment_id'], ['apartments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('apartment_id', 'class_name', name='uq_apartment_class'),
    )

    # Apartment history table
    op.create_table(
        'apartment_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('apartment_id', sa.Integer(), nullable=False),
        sa.Column('snapshot_data', postgresql.JSON(), nullable=False),
        sa.Column('free_apartments', sa.Integer(), nullable=True),
        sa.Column('occupied_apartments', sa.Integer(), nullable=True),
        sa.Column('total_apartments', sa.Integer(), nullable=True),
        sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['apartment_id'], ['apartments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_history_apt_time', 'apartment_history', ['apartment_id', 'recorded_at'])
    op.create_index('idx_history_recorded', 'apartment_history', ['recorded_at'])

    # Changes table
    op.create_table(
        'changes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('apartment_id', sa.Integer(), nullable=False),
        sa.Column('field_name', sa.String(255), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('change_type', sa.String(50), nullable=False, server_default='update'),
        sa.Column('detected_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('notified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['apartment_id'], ['apartments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_changes_detected', 'changes', ['detected_at'])
    op.create_index('idx_changes_apartment', 'changes', ['apartment_id', 'detected_at'])

    # Scraper settings table
    op.create_table(
        'scraper_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('key', sa.String(100), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )

    # Notifications table
    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('change_id', sa.Integer(), nullable=True),
        sa.Column('apartment_id', sa.Integer(), nullable=False),
        sa.Column('message_text', sa.Text(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('sent_successfully', sa.Boolean(), nullable=False, server_default='true'),
        sa.ForeignKeyConstraint(['apartment_id'], ['apartments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['change_id'], ['changes.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Scraper logs table
    op.create_table(
        'scraper_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('apartments_checked', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('apartments_success', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('apartments_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('changes_detected', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('ran_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('is_payday_run', sa.Boolean(), nullable=False, server_default='false'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_logs_ran', 'scraper_logs', ['ran_at'])


def downgrade() -> None:
    op.drop_table('notifications')
    op.drop_table('scraper_settings')
    op.drop_table('changes')
    op.drop_table('apartment_history')
    op.drop_table('apartment_types')
    op.drop_table('scraper_logs')
    op.drop_table('apartments')