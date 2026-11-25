"""order_event.in_trade_at: gone 之前交易中过（推测成交用）

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

from skinpulse.db.models import UtcDateTime


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # intent 2026-11-23：老事件全是 NULL，只影响以后消失的单
    with op.batch_alter_table('order_event') as batch:
        batch.add_column(sa.Column('in_trade_at', UtcDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('order_event') as batch:
        batch.drop_column('in_trade_at')
