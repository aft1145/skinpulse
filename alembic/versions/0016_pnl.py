"""盈亏统计（盈亏 spec §3）：买卖订单、每天的持仓快照和汇总

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

from skinpulse.db.models import UtcDateTime


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

M = sa.Numeric(12, 2)


def upgrade() -> None:
    # 新表，老数据不动
    op.create_table(
        'trade_order',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('order_no', sa.String(32), nullable=False),
        sa.Column('side', sa.String(4), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('hash_name', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('amount', M, nullable=False),
        sa.Column('net_amount', M, nullable=True),
        sa.Column('net_exact', sa.Boolean(), nullable=False),
        sa.Column('detail_checked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('sub_type', sa.Integer(), nullable=True),
        sa.Column('asset_id', sa.String(32), nullable=True),
        sa.Column('created_at', UtcDateTime(), nullable=False),
        sa.Column('finished_at', UtcDateTime(), nullable=True),
        sa.Column('fetched_at', UtcDateTime(), nullable=False),
        sa.UniqueConstraint('platform', 'order_no', name='uq_trade_order_platform_no'),
    )
    op.create_index('ix_trade_order_account_id', 'trade_order', ['account_id'])
    op.create_index('ix_trade_order_template_id', 'trade_order', ['template_id'])
    op.create_index('ix_trade_order_created_at', 'trade_order', ['created_at'])
    op.create_table(
        'pnl_position',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('date', sa.String(10), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('hash_name', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('cost', M, nullable=False),
        sa.Column('first_bought_at', UtcDateTime(), nullable=False),
        sa.Column('inv_qty', sa.Integer(), nullable=False),
        sa.Column('lease_qty', sa.Integer(), nullable=False),
        sa.Column('unknown_qty', sa.Integer(), nullable=False),
        sa.Column('price', M, nullable=True),
        sa.Column('price_src', sa.String(16), nullable=True),
        sa.Column('price_flag', sa.String(16), nullable=True),
        sa.UniqueConstraint('date', 'account_id', 'platform', 'template_id', name='uq_pnl_position_day_acc_tpl'),
    )
    op.create_index('ix_pnl_position_date', 'pnl_position', ['date'])
    op.create_table(
        'pnl_daily',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('date', sa.String(10), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('realized_total', M, nullable=False),
        sa.Column('realized_day', M, nullable=False),
        sa.Column('qty', sa.Integer(), nullable=False),
        sa.Column('cost', M, nullable=False),
        sa.Column('value_mkt', M, nullable=False),
        sa.Column('value_net', M, nullable=False),
        sa.Column('cost_priced', M, nullable=False),
        sa.Column('unpriced_qty', sa.Integer(), nullable=False),
        sa.Column('inv_qty', sa.Integer(), nullable=False),
        sa.Column('lease_qty', sa.Integer(), nullable=False),
        sa.Column('unknown_qty', sa.Integer(), nullable=False),
        sa.Column('unknown_cost', M, nullable=False),
        sa.Column('youpin_pnl', M, nullable=True),
        sa.Column('youpin_pnl_n', sa.Integer(), nullable=True),
        sa.Column('lease_count', sa.Integer(), nullable=False),
        sa.Column('lease_rent_day', M, nullable=True),
        sa.Column('unbooked_inv_qty', sa.Integer(), nullable=False),
        sa.Column('unbooked_inv_value', M, nullable=True),
        sa.Column('computed_at', UtcDateTime(), nullable=False),
        sa.UniqueConstraint('date', 'account_id', name='uq_pnl_daily_day_acc'),
    )
    op.create_index('ix_pnl_daily_date', 'pnl_daily', ['date'])


def downgrade() -> None:
    op.drop_table('pnl_daily')
    op.drop_table('pnl_position')
    op.drop_table('trade_order')
