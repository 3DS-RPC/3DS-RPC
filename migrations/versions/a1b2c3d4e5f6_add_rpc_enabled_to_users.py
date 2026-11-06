"""Add RPC enabled toggle to users

Revision ID: a1b2c3d4e5f6
Revises: 9f3c7e1a2b4d
Create Date: 2026-08-31 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '9f3c7e1a2b4d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rpc_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.drop_column('rpc_enabled')