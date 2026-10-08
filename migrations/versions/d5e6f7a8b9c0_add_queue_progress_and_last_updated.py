"""Add queue progress to backend_metrics and last_updated to friends

Revision ID: d5e6f7a8b9c0
Revises: b3d4a1c2e5f6
Create Date: 2026-10-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5e6f7a8b9c0'
down_revision = 'b3d4a1c2e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('backend_metrics', schema=None) as batch_op:
        batch_op.add_column(sa.Column('full_loop_current', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('full_loop_total', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('full_loop_last_update', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('quick_loop_current', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('quick_loop_total', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('quick_loop_last_update', sa.Float(), nullable=False, server_default='0'))

    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_updated', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.drop_column('last_updated')

    with op.batch_alter_table('backend_metrics', schema=None) as batch_op:
        batch_op.drop_column('quick_loop_last_update')
        batch_op.drop_column('quick_loop_total')
        batch_op.drop_column('quick_loop_current')
        batch_op.drop_column('full_loop_last_update')
        batch_op.drop_column('full_loop_total')
        batch_op.drop_column('full_loop_current')
