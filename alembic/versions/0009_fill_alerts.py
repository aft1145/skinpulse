"""fill alerts: my_buy_order in-trade columns, order_event outcome/notice

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

import skinpulse.db.models


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 成交推送 spec §3：交易中（rank = "*"）从哪一刻开始、推没推；消失的单查清后的去向和成交推送状态。
    # 老行全是 NULL：旧事件不会被推送，去向交给 `fills backfill`。
    with op.batch_alter_table('my_buy_order') as batch:
        batch.add_column(sa.Column('in_trade_since', skinpulse.db.models.UtcDateTime(), nullable=True))
        batch.add_column(sa.Column('in_trade_notice', sa.String(length=16), nullable=True))
    with op.batch_alter_table('order_event') as batch:
        batch.add_column(sa.Column('outcome', sa.String(length=16), nullable=True))
        batch.add_column(sa.Column('outcome_at', skinpulse.db.models.UtcDateTime(), nullable=True))
        batch.add_column(sa.Column('outcome_qty', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('notice', sa.String(length=16), nullable=True))
        # Monitor 每轮按 notice 找待推的成交；order_event 只增不减，不建索引就是每轮全表扫
        batch.create_index('ix_order_event_notice', ['notice'])


def downgrade() -> None:
    with op.batch_alter_table('order_event') as batch:
        batch.drop_index('ix_order_event_notice')
        for col in ('notice', 'outcome_qty', 'outcome_at', 'outcome'):
            batch.drop_column(col)
    with op.batch_alter_table('my_buy_order') as batch:
        batch.drop_column('in_trade_notice')
        batch.drop_column('in_trade_since')
