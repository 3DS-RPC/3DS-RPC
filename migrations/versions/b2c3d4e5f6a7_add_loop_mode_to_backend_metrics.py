"""Add loop_mode to backend_metrics

Revision ID: b2c3d4e5f6a7
Revises: f6b7c8d9e0f1
Create Date: 2026-10-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'f6b7c8d9e0f1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('backend_metrics', schema=None) as batch_op:
        batch_op.add_column(sa.Column('loop_mode', sa.String(length=8), nullable=False, server_default=''))


def downgrade():
    with op.batch_alter_table('backend_metrics', schema=None) as batch_op:
        batch_op.drop_column('loop_mode')
