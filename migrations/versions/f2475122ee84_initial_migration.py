"""Initial migration

Revision ID: f2475122ee84
Revises:
Create Date: 2024-09-23 23:00:12.054892

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f2475122ee84'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.alter_column('refresh',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=False)
        batch_op.alter_column('bearer',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=False)
        batch_op.alter_column('session',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=True)
        batch_op.alter_column('token',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=True)

    with op.batch_alter_table('discord_friends', schema=None) as batch_op:
        batch_op.alter_column('friend_code',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=False)

    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.alter_column('friend_code',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=False)
        batch_op.alter_column('title_id',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=False)
        batch_op.alter_column('upd_id',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=False)
        batch_op.alter_column('username',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=True)
        batch_op.alter_column('message',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=True)
        batch_op.alter_column('mii',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=True)
        batch_op.alter_column('game_description',
                              existing_type=sa.TEXT(),
                              type_=sa.String(),
                              existing_nullable=True)
        batch_op.drop_index('friends_friendcode_network_idx')


def downgrade():
    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.create_index('friends_friendcode_network_idx', ['friend_code', 'network'], unique=False)
        batch_op.alter_column('game_description',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=True)
        batch_op.alter_column('mii',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=True)
        batch_op.alter_column('message',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=True)
        batch_op.alter_column('username',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=True)
        batch_op.alter_column('upd_id',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=False)
        batch_op.alter_column('title_id',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=False)
        batch_op.alter_column('friend_code',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=False)

    with op.batch_alter_table('discord_friends', schema=None) as batch_op:
        batch_op.alter_column('friend_code',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=False)

    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.alter_column('token',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=True)
        batch_op.alter_column('session',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=True)
        batch_op.alter_column('bearer',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=False)
        batch_op.alter_column('refresh',
                              existing_type=sa.String(),
                              type_=sa.TEXT(),
                              existing_nullable=False)
