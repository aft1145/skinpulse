"""充值前检查暂停单（暂停单 spec §4）：检查记录和每张暂停单的结果。新表，老数据不动。

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

from skinpulse.db.models import UtcDateTime


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'paused_check_run',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('started_at', UtcDateTime(), nullable=False),
        sa.Column('finished_at', UtcDateTime(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('phase', sa.String(length=16), nullable=True),
        sa.Column('done', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('incomplete', sa.Boolean(), nullable=False),
        sa.Column('steamdt', sa.Boolean(), nullable=False),
        sa.Column('skipped', sa.String(length=64), nullable=True),
        sa.Column('error', sa.String(length=255), nullable=True),
    )
    op.create_index('ix_paused_check_run_started_at', 'paused_check_run', ['started_at'])
    op.create_index('ix_paused_check_run_status', 'paused_check_run', ['status'])
    op.create_table(
        'paused_check_item',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('purchase_no', sa.String(length=64), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('commodity_name', sa.String(length=255), nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('remaining_qty', sa.Integer(), nullable=False),
        sa.Column('paused_at', UtcDateTime(), nullable=True),
        sa.Column('top_bid', sa.Numeric(12, 2), nullable=True),
        sa.Column('top_empty', sa.Boolean(), nullable=False),
        sa.Column('sell_min', sa.Numeric(12, 2), nullable=True),
        sa.Column('sell_depth', sa.Integer(), nullable=True),
        sa.Column('sell_src', sa.String(length=16), nullable=True),
        sa.Column('sell_at', UtcDateTime(), nullable=True),
        sa.Column('tier', sa.String(length=16), nullable=False),
        sa.Column('top1_kind', sa.String(length=8), nullable=True),
        sa.UniqueConstraint('run_id', 'purchase_no', name='uq_paused_check_item_run_no'),
    )
    op.create_index('ix_paused_check_item_run_id', 'paused_check_item', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_paused_check_item_run_id', 'paused_check_item')
    op.drop_table('paused_check_item')
    op.drop_index('ix_paused_check_run_status', 'paused_check_run')
    op.drop_index('ix_paused_check_run_started_at', 'paused_check_run')
    op.drop_table('paused_check_run')
