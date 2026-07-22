"""Add class_name filter to subscriptions

Revision ID: 006
Revises: 005
Create Date: 2026-07-22

"""
from alembic import op
import sqlalchemy as sa


revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'realestate_subscriptions',
        sa.Column('class_name', sa.String(30), nullable=True),
    )
    # Drop the old unique constraint (user_id + server_sid)
    op.drop_constraint('uq_subscription_user_server', 'realestate_subscriptions', type_='unique')
    # Create new one covering class_name too
    op.create_unique_constraint(
        'uq_subscription_user_server_kind_class',
        'realestate_subscriptions',
        ['user_id', 'server_sid', 'kind', 'class_name'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_subscription_user_server_kind_class', 'realestate_subscriptions', type_='unique')
    op.create_unique_constraint(
        'uq_subscription_user_server',
        'realestate_subscriptions',
        ['user_id', 'server_sid'],
    )
    op.drop_column('realestate_subscriptions', 'class_name')
