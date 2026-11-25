"""market alerts: my_buy_order.market_flag / market_notice

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 市价提醒 spec §2：老行全是 NULL，上线第一轮按现在的情况判，成立的就是新的一段、会推一次。
    with op.batch_alter_table('my_buy_order') as batch:
        batch.add_column(sa.Column('market_flag', sa.String(length=8), nullable=True))
        batch.add_column(sa.Column('market_notice', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('my_buy_order') as batch:
        batch.drop_column('market_notice')
        batch.drop_column('market_flag')
