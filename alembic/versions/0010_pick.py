"""pick: pick_run and pick_result (valuation phase 3)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

import skinpulse.db.models


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 价位 spec §9b：选品的运行记录和结果。新表，老数据不动。
    op.create_table(
        'pick_run',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('created_at', skinpulse.db.models.UtcDateTime(), nullable=False),
        sa.Column('finished_at', skinpulse.db.models.UtcDateTime(), nullable=True),
        sa.Column('filters_json', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('phase', sa.String(length=16), nullable=False),
        sa.Column('done', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('pool', sa.Integer(), nullable=False),
        sa.Column('priced', sa.Integer(), nullable=False),
        sa.Column('failed', sa.Integer(), nullable=False),
        sa.Column('price_failed', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(length=255), nullable=True),
    )
    op.create_index('ix_pick_run_created_at', 'pick_run', ['created_at'])
    op.create_index('ix_pick_run_status', 'pick_run', ['status'])
    op.create_table(
        'pick_result',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('yp_sell', sa.Numeric(12, 2), nullable=True),
        sa.Column('yp_count', sa.Integer(), nullable=True),
        sa.Column('yp_bid', sa.Numeric(12, 2), nullable=True),
        sa.Column('bars', sa.Integer(), nullable=False),
        sa.Column('pct', sa.Numeric(10, 6), nullable=True),
        sa.Column('trend', sa.String(length=8), nullable=True),
        sa.Column('rel', sa.String(length=8), nullable=True),
        sa.Column('q10', sa.Numeric(10, 6), nullable=True),
        sa.Column('liq', sa.String(length=8), nullable=True),
        sa.Column('exit', sa.Numeric(12, 2), nullable=True),
        sa.Column('exit_where', sa.String(length=16), nullable=True),
        sa.Column('cap', sa.Numeric(12, 2), nullable=True),
        sa.Column('verdict', sa.String(length=8), nullable=False),
        sa.Column('rule', sa.String(length=255), nullable=False),
        sa.Column('bid', sa.Numeric(12, 2), nullable=True),
        sa.Column('margin', sa.Numeric(12, 6), nullable=True),
        sa.Column('first', sa.Boolean(), nullable=False),
        sa.Column('mine', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('run_id', 'template_id', name='uq_pick_result_run_tpl'),
    )
    op.create_index('ix_pick_result_run_id', 'pick_result', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_pick_result_run_id', 'pick_result')
    op.drop_table('pick_result')
    op.drop_index('ix_pick_run_status', 'pick_run')
    op.drop_index('ix_pick_run_created_at', 'pick_run')
    op.drop_table('pick_run')
