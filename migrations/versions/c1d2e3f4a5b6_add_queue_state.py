"""Add sliding-window queue state."""
import sqlalchemy as sa
from alembic import op

revision = 'c1d2e3f4a5b6'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'friend_schedule',
        sa.Column('network', sa.Integer(), nullable=False),
        sa.Column('friend_code', sa.String(length=12), nullable=False),
        sa.Column('last_polled_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('next_due_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('consecutive_misses', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('network', 'friend_code'),
        sa.ForeignKeyConstraint(
            ['friend_code'], ['friends.friend_code'], ondelete='CASCADE',
        ),
    )
    op.create_index(
        'friend_schedule_due',
        'friend_schedule',
        ['network', 'next_due_at'],
    )

    op.create_table(
        'remote_window',
        sa.Column('network', sa.Integer(), nullable=False),
        sa.Column('friend_code', sa.String(length=12), nullable=False),
        sa.Column('pid', sa.BigInteger(), nullable=False),
        sa.Column('added_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('network', 'friend_code'),
    )
    op.create_index(
        'remote_window_added',
        'remote_window',
        ['network', 'added_at'],
    )


def downgrade():
    op.drop_index('remote_window_added', table_name='remote_window')
    op.drop_table('remote_window')
    op.drop_index('friend_schedule_due', table_name='friend_schedule')
    op.drop_table('friend_schedule')
