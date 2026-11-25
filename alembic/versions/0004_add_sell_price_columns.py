"""add sell price columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21 16:10:00.000000
"""
from alembic import op
import sqlalchemy as sa
import skinpulse.db.models


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sell_depth 和 sell_min 分开两列，是为了把"没人在卖"（depth=0）和"没查到价格"
    # （depth 为 NULL）区分开——两者在 sell_min 上都是 NULL。详见 MyBuyOrder 里的注释。
    with op.batch_alter_table('my_buy_order', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sell_min', sa.Numeric(precision=12, scale=2), nullable=True))
        batch_op.add_column(sa.Column('sell_depth', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('sell_checked_at', skinpulse.db.models.UtcDateTime(), nullable=True))

    with op.batch_alter_table('rank_snapshot', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sell_depth', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('rank_snapshot', schema=None) as batch_op:
        batch_op.drop_column('sell_depth')

    with op.batch_alter_table('my_buy_order', schema=None) as batch_op:
        batch_op.drop_column('sell_checked_at')
        batch_op.drop_column('sell_depth')
        batch_op.drop_column('sell_min')
