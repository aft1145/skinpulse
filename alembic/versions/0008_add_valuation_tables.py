"""add daily_bar, index_daily, valuation, order_event

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
import skinpulse.db.models


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def _ohlc() -> list[sa.Column]:
    return [sa.Column(k, sa.Numeric(precision=12, scale=2), nullable=False) for k in ('open', 'close', 'high', 'low')]


def upgrade() -> None:
    # 价位一期（价位 spec §3.1）：自有日线、每日估值、求购单事件。
    op.create_table(
        'daily_bar',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.String(length=16), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        *_ohlc(),
        sa.Column('fetched_at', skinpulse.db.models.UtcDateTime(), nullable=False),
        sa.UniqueConstraint('template_id', 'platform', 'date', name='uq_daily_bar_tpl_platform_day'),
    )
    op.create_table(
        'index_daily',
        sa.Column('date', sa.String(length=10), primary_key=True),
        *_ohlc(),
        sa.Column('fetched_at', skinpulse.db.models.UtcDateTime(), nullable=False),
    )
    ratio = lambda name: sa.Column(name, sa.Numeric(precision=10, scale=6), nullable=True)  # noqa: E731
    money = lambda name: sa.Column(name, sa.Numeric(precision=12, scale=2), nullable=True)  # noqa: E731
    op.create_table(
        'valuation',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('computed_at', skinpulse.db.models.UtcDateTime(), nullable=False),
        sa.Column('bars', sa.Integer(), nullable=False),
        ratio('pct'), money('lo'), money('hi'), money('cur'),
        sa.Column('trend', sa.String(length=8), nullable=True),
        ratio('c30'), ratio('c90'), ratio('b30'), ratio('b90'), ratio('ex30'),
        sa.Column('rel', sa.String(length=8), nullable=True),
        ratio('vol7'), ratio('q10'),
        sa.Column('liq', sa.String(length=8), nullable=True),
        sa.Column('listed', sa.Integer(), nullable=True),
        ratio('spread'), money('exit'),
        sa.Column('exit_where', sa.String(length=16), nullable=True),
        money('exit_sell'), money('cap'),
        sa.Column('snapshot_date', sa.String(length=10), nullable=True),
        sa.UniqueConstraint('date', 'template_id', name='uq_valuation_day_tpl'),
    )
    op.create_index('ix_valuation_date', 'valuation', ['date'])
    op.create_index('ix_valuation_template_id', 'valuation', ['template_id'])
    op.create_table(
        'order_event',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('purchase_no', sa.String(length=64), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('qty_before', sa.Integer(), nullable=True),
        sa.Column('qty_after', sa.Integer(), nullable=True),
        sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('at', skinpulse.db.models.UtcDateTime(), nullable=False),
    )
    op.create_index('ix_order_event_purchase_no', 'order_event', ['purchase_no'])
    op.create_index('ix_order_event_template_id', 'order_event', ['template_id'])
    op.create_index('ix_order_event_at', 'order_event', ['at'])


def downgrade() -> None:
    for ix in ('ix_order_event_at', 'ix_order_event_template_id', 'ix_order_event_purchase_no'):
        op.drop_index(ix, table_name='order_event')
    op.drop_table('order_event')
    op.drop_index('ix_valuation_template_id', table_name='valuation')
    op.drop_index('ix_valuation_date', table_name='valuation')
    op.drop_table('valuation')
    op.drop_table('index_daily')
    op.drop_table('daily_bar')
