"""Add console health to discord_friends

Revision ID: e4f5c6d7a8b9
Revises: d5e6f7a8b9c0
Create Date: 2026-10-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4f5c6d7a8b9'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('discord_friends', schema=None) as batch_op:
        batch_op.add_column(sa.Column('health', sa.String(length=16), nullable=False, server_default='ok'))
        batch_op.add_column(sa.Column('health_reason', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('health_updated', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('discord_friends', schema=None) as batch_op:
        batch_op.drop_column('health_updated')
        batch_op.drop_column('health_reason')
        batch_op.drop_column('health')