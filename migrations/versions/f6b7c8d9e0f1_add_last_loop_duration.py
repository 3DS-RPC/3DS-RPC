"""Add last_loop_duration to backend_metrics

Revision ID: f6b7c8d9e0f1
Revises: e4f5c6d7a8b9
Create Date: 2026-10-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6b7c8d9e0f1'
down_revision = 'e4f5c6d7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('backend_metrics', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_loop_duration', sa.Float(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('backend_metrics', schema=None) as batch_op:
        batch_op.drop_column('last_loop_duration')
