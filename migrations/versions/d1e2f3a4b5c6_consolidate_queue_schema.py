"""Consolidate the sliding-window queue schema."""
import sqlalchemy as sa
from alembic import op

revision = 'd1e2f3a4b5c6'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def _drop_legacy_rotation():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table('backend_loop_items'):
        op.drop_table('backend_loop_items')
    if inspector.has_table('backend_loops'):
        op.drop_table('backend_loops')


def upgrade():
    with op.batch_alter_table('backend_metrics') as batch_op:
        batch_op.alter_column('full_loop_current', new_column_name='slow_loop_current')
        batch_op.alter_column('full_loop_total', new_column_name='slow_loop_total')
        batch_op.alter_column('full_loop_last_update', new_column_name='slow_loop_last_update')
        batch_op.alter_column('quick_loop_current', new_column_name='fast_loop_current')
        batch_op.alter_column('quick_loop_total', new_column_name='fast_loop_total')
        batch_op.alter_column('quick_loop_last_update', new_column_name='fast_loop_last_update')
        batch_op.add_column(sa.Column('slow_loop_skipped', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('slow_loop_failed', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('fast_loop_skipped', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('fast_loop_failed', sa.Integer(), nullable=False, server_default='0'))

    with op.batch_alter_table('friends') as batch_op:
        batch_op.add_column(sa.Column('profile_retry_at', sa.BigInteger(), nullable=False, server_default='0'))

    _drop_legacy_rotation()


def downgrade():
    with op.batch_alter_table('friends') as batch_op:
        batch_op.drop_column('profile_retry_at')

    with op.batch_alter_table('backend_metrics') as batch_op:
        batch_op.drop_column('fast_loop_failed')
        batch_op.drop_column('fast_loop_skipped')
        batch_op.drop_column('slow_loop_failed')
        batch_op.drop_column('slow_loop_skipped')
        batch_op.alter_column('slow_loop_last_update', new_column_name='full_loop_last_update')
        batch_op.alter_column('slow_loop_total', new_column_name='full_loop_total')
        batch_op.alter_column('slow_loop_current', new_column_name='full_loop_current')
        batch_op.alter_column('fast_loop_last_update', new_column_name='quick_loop_last_update')
        batch_op.alter_column('fast_loop_total', new_column_name='quick_loop_total')
        batch_op.alter_column('fast_loop_current', new_column_name='quick_loop_current')
