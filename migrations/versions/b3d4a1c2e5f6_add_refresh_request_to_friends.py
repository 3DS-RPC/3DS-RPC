"""Add refresh request flag and cooldown to friends

Revision ID: b3d4a1c2e5f6
Revises: c4d5e6f7a8b9
Create Date: 2026-10-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d4a1c2e5f6'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.add_column(sa.Column('refresh_requested', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('last_refresh', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.drop_column('last_refresh')
        batch_op.drop_column('refresh_requested')