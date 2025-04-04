"""Re-create index over network column

Revision ID: ed39a1af1115
Revises: f2475122ee84
Create Date: 2024-09-24 11:16:36.100960

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ed39a1af1115'
down_revision = 'f2475122ee84'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_friends_network'), ['network'], unique=False)


def downgrade():
    with op.batch_alter_table('friends', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_friends_network'))
