"""add reference bid columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23 22:40:00.000000
"""
from alembic import op
import sqlalchemy as sa
import skinpulse.db.models


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 别的平台的最高求购价（BUFF/C5 中位数）。平台名单一起存：参照是哪几家会随覆盖情况变化，
    # 只存一个数字而不记来源，等于把一家的报价谎报成几家的共识。
    with op.batch_alter_table('my_buy_order', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ref_bid', sa.Numeric(precision=12, scale=2), nullable=True))
        batch_op.add_column(sa.Column('ref_bid_platforms', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('ref_bid_at', skinpulse.db.models.UtcDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('my_buy_order', schema=None) as batch_op:
        batch_op.drop_column('ref_bid_at')
        batch_op.drop_column('ref_bid_platforms')
        batch_op.drop_column('ref_bid')
