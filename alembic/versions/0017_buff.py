"""BUFF（BUFF spec §10）：账号的平台用户号、饰品的 BUFF goods_id、条件单挡路张数、配上的 BUFF 订单号

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('account') as batch:
        batch.add_column(sa.Column('platform_uid', sa.String(32), nullable=True))
    with op.batch_alter_table('item') as batch:
        batch.add_column(sa.Column('buff_goods_id', sa.Integer(), nullable=True))
        batch.create_index('ix_item_buff_goods_id', ['buff_goods_id'])
    with op.batch_alter_table('my_buy_order') as batch:
        batch.add_column(sa.Column('cond_ahead', sa.Integer(), nullable=True))
    with op.batch_alter_table('order_event') as batch:
        batch.add_column(sa.Column('outcome_ref', sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('order_event') as batch:
        batch.drop_column('outcome_ref')
    with op.batch_alter_table('my_buy_order') as batch:
        batch.drop_column('cond_ahead')
    with op.batch_alter_table('item') as batch:
        batch.drop_index('ix_item_buff_goods_id')
        batch.drop_column('buff_goods_id')
    with op.batch_alter_table('account') as batch:
        batch.drop_column('platform_uid')
