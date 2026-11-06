"""Add API keys to users

Revision ID: 9f3c7e1a2b4d
Revises: 8bd4627f0cf9
Create Date: 2026-08-31 00:00:00.000000

"""
import secrets

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '9f3c7e1a2b4d'
down_revision = '8bd4627f0cf9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.add_column(sa.Column('api_key', sa.String(length=32), nullable=False, server_default=''))

    # Backfill keys for users that predate this migration.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        'SELECT id FROM discord'
    )).fetchall()
    for row in rows:
        bind.execute(
            sa.text('UPDATE discord SET api_key = :key WHERE id = :id'),
            {'key': secrets.token_hex(16), 'id': row.id},
        )


def downgrade():
    with op.batch_alter_table('discord', schema=None) as batch_op:
        batch_op.drop_column('api_key')