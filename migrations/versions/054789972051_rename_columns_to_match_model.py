"""Rename columns to match model

Revision ID: 054789972051
Revises: ed39a1af1115
Create Date: 2024-09-24 13:12:12.075111

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '054789972051'
down_revision = 'ed39a1af1115'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.alter_column('refresh', new_column_name='refresh_token')
        batch_op.alter_column('bearer', new_column_name='bearer_token')
        batch_op.alter_column('session', new_column_name='rpc_session_token')
        batch_op.alter_column('token', new_column_name='site_session_token')
        batch_op.create_unique_constraint('discord_pk', ['site_session_token'])


def downgrade():
    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.alter_column('refresh_token', new_column_name='refresh')
        batch_op.alter_column('bearer_token', new_column_name='bearer')
        batch_op.alter_column('rpc_session_token', new_column_name='session')
        batch_op.alter_column('site_session_token', new_column_name='token')
        batch_op.drop_constraint('discord_pk', type_='unique')
