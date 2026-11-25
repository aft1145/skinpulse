"""多账号（多账号 spec §2）：account 加平台、简称、启停、设备身份；单和事件记账号；账号通道状态按号

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

from skinpulse.db.models import UtcDateTime


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('account') as batch:
        batch.add_column(sa.Column('platform', sa.String(16), nullable=False, server_default='youpin'))
        batch.add_column(sa.Column('label', sa.String(16), nullable=True))
        batch.add_column(sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('device_id', sa.String(64), nullable=True))
        batch.add_column(sa.Column('created_at', UtcDateTime(), nullable=True))
    # 老数据全是唯一那个号的
    for table in ('my_buy_order', 'order_event'):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('account_id', sa.Integer(), nullable=False, server_default='1'))
            batch.create_index(f'ix_{table}_account_id', ['account_id'])
    op.execute("UPDATE system_status SET adapter = 'youpin_account:1' WHERE adapter = 'youpin_account'")


def downgrade() -> None:
    op.execute("UPDATE system_status SET adapter = 'youpin_account' WHERE adapter = 'youpin_account:1'")
    op.execute("DELETE FROM system_status WHERE adapter LIKE 'youpin_account:%'")
    for table in ('order_event', 'my_buy_order'):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f'ix_{table}_account_id')
            batch.drop_column('account_id')
    with op.batch_alter_table('account') as batch:
        for col in ('created_at', 'device_id', 'enabled', 'label', 'platform'):
            batch.drop_column(col)
