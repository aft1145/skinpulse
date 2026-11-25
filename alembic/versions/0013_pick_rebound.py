"""pick_result.rebound / amp：选品的「刚从低点涨上来」和「炒过」

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # intent 2026-11-23-pick-hype-and-filters：老结果全是 NULL，页面不显示这两项
    with op.batch_alter_table('pick_result') as batch:
        batch.add_column(sa.Column('rebound', sa.Numeric(10, 6), nullable=True))
        batch.add_column(sa.Column('amp', sa.Numeric(10, 6), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('pick_result') as batch:
        batch.drop_column('amp')
        batch.drop_column('rebound')
