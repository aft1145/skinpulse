"""add brief_log.rows_json

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 看板三期：简报页按表格展示当天发出去的那张前 N 名表。body 只有纯文本，事后重算又不忠实
    # （撤掉的单会消失、阈值用的是现在的），所以发的时候把表存下来。老记录为空。
    with op.batch_alter_table('brief_log') as batch:
        batch.add_column(sa.Column('rows_json', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('brief_log') as batch:
        batch.drop_column('rows_json')
