"""add price_daily and brief_log

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
import skinpulse.db.models


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # M3a 每日简报（spec §7a）：自有价格历史，以及每天「发了什么 / 为什么没发」。
    op.create_table(
        'price_daily',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('market_hash_name', sa.String(length=255), nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False),
        sa.Column('sell_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('sell_count', sa.Integer(), nullable=False),
        sa.Column('bidding_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('bidding_count', sa.Integer(), nullable=False),
        sa.Column('update_time', skinpulse.db.models.UtcDateTime(), nullable=True),
        sa.Column('fetched_at', skinpulse.db.models.UtcDateTime(), nullable=False),
        sa.UniqueConstraint('date', 'template_id', 'platform', name='uq_price_daily_day_tpl_platform'),
    )
    op.create_index('ix_price_daily_date', 'price_daily', ['date'])
    op.create_index('ix_price_daily_template_id', 'price_daily', ['template_id'])
    op.create_table(
        'brief_log',
        sa.Column('date', sa.String(length=10), primary_key=True),
        sa.Column('decision', sa.String(length=16), nullable=False),
        sa.Column('reasons_json', sa.Text(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('created_at', skinpulse.db.models.UtcDateTime(), nullable=False),
        sa.Column('sent_at', skinpulse.db.models.UtcDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('brief_log')
    op.drop_index('ix_price_daily_template_id', table_name='price_daily')
    op.drop_index('ix_price_daily_date', table_name='price_daily')
    op.drop_table('price_daily')
