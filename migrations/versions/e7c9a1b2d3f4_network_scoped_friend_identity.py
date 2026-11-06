"""Make friend identity network-scoped

A friend code is only unique within a network, but the schema used
``friend_code`` as the global primary key. That silently blocked a Pretendo
console from being tracked if the same 12-digit code existed on Nintendo.

Revision ID: e7c9a1b2d3f4
Revises: d1e2f3a4b5c6
"""

import sqlalchemy as sa
from alembic import op

revision = 'e7c9a1b2d3f4'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def _friend_code_unique_names(inspector):
    """Names of UNIQUE constraints/indexes that only cover friend_code."""
    for constraint in inspector.get_unique_constraints('friends'):
        if constraint['column_names'] == ['friend_code']:
            yield 'constraint', constraint['name']
    for index in inspector.get_indexes('friends'):
        if index.get('unique') and index['column_names'] == ['friend_code']:
            yield 'index', index['name']


def _upgrade_postgres(inspector):
    # The schedule FK depends on friends.friend_code's uniqueness, so it has to
    # go before the primary key can change.
    for fk in inspector.get_foreign_keys('friend_schedule'):
        if fk['referred_table'] == 'friends' and fk['name']:
            op.drop_constraint(fk['name'], 'friend_schedule', type_='foreignkey')

    for kind, name in _friend_code_unique_names(inspector):
        if name is None:
            continue
        if kind == 'constraint':
            op.drop_constraint(name, 'friends', type_='unique')
        else:
            op.drop_index(name, table_name='friends')

    friends_pk = inspector.get_pk_constraint('friends')
    op.drop_constraint(friends_pk['name'], 'friends', type_='primary')
    op.create_primary_key('friends_pkey', 'friends', ['friend_code', 'network'])

    discord_friends_pk = inspector.get_pk_constraint('discord_friends')
    op.drop_constraint(discord_friends_pk['name'], 'discord_friends', type_='primary')
    op.create_primary_key('discord_friends_pkey', 'discord_friends', ['id', 'friend_code', 'network'])

    op.create_foreign_key(
        'friend_schedule_friend_fkey',
        'friend_schedule', 'friends',
        ['network', 'friend_code'], ['network', 'friend_code'],
        ondelete='CASCADE',
    )


def _sqlite_rebuilt_table(name, pk_columns, indexes=(), foreign_keys=(), uniques=()):
    """Builds the target Table so batch mode can rebuild an unnamed schema.

    SQLite stores primary/foreign keys without names, so Alembic cannot drop
    them. ``copy_from`` lets us describe the desired table directly instead.
    """
    reflected = sa.Table(name, sa.MetaData(), autoload_with=op.get_bind())
    columns = []
    for column in reflected.columns:
        kwargs = {'nullable': column.nullable}
        if column.server_default is not None:
            kwargs['server_default'] = column.server_default.arg
        columns.append(sa.Column(column.name, column.type, **kwargs))
    return sa.Table(
        name,
        sa.MetaData(),
        *columns,
        sa.PrimaryKeyConstraint(*pk_columns),
        *foreign_keys,
        *uniques,
        *indexes,
    )


def _upgrade_sqlite():
    # SQLite cannot alter primary/foreign keys in place; batch mode rebuilds
    # each table from the reflected schema.
    friends = _sqlite_rebuilt_table(
        'friends',
        ['friend_code', 'network'],
        indexes=(sa.Index('ix_friends_network', 'network'),),
    )
    with op.batch_alter_table('friends', copy_from=friends, recreate='always'):
        pass

    discord_friends = _sqlite_rebuilt_table(
        'discord_friends',
        ['id', 'friend_code', 'network'],
    )
    with op.batch_alter_table('discord_friends', copy_from=discord_friends, recreate='always'):
        pass

    friend_schedule = _sqlite_rebuilt_table(
        'friend_schedule',
        ['network', 'friend_code'],
        indexes=(sa.Index('friend_schedule_due', 'network', 'next_due_at'),),
        foreign_keys=(
            sa.ForeignKeyConstraint(
                ['network', 'friend_code'],
                ['friends.network', 'friends.friend_code'],
                ondelete='CASCADE',
                name='friend_schedule_friend_fkey',
            ),
        ),
    )
    with op.batch_alter_table('friend_schedule', copy_from=friend_schedule, recreate='always'):
        pass


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name == 'sqlite':
        _upgrade_sqlite()
    else:
        _upgrade_postgres(inspector)


def _downgrade_sqlite():
    friend_schedule = _sqlite_rebuilt_table(
        'friend_schedule',
        ['network', 'friend_code'],
        indexes=(sa.Index('friend_schedule_due', 'network', 'next_due_at'),),
        foreign_keys=(
            sa.ForeignKeyConstraint(
                ['friend_code'],
                ['friends.friend_code'],
                ondelete='CASCADE',
                name='friend_schedule_friend_code_fkey',
            ),
        ),
    )
    with op.batch_alter_table('friend_schedule', copy_from=friend_schedule, recreate='always'):
        pass

    discord_friends = _sqlite_rebuilt_table('discord_friends', ['id', 'friend_code'])
    with op.batch_alter_table('discord_friends', copy_from=discord_friends, recreate='always'):
        pass

    # This fails if both networks gained the same friend code while the new
    # schema was live - that is intentional, and a manual data merge is needed.
    friends = _sqlite_rebuilt_table(
        'friends',
        ['friend_code'],
        indexes=(sa.Index('ix_friends_network', 'network'),),
        uniques=(sa.UniqueConstraint('friend_code', name='uq_friends_friend_code'),),
    )
    with op.batch_alter_table('friends', copy_from=friends, recreate='always'):
        pass


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        _downgrade_sqlite()
        return

    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys('friend_schedule'):
        if fk['referred_table'] == 'friends' and fk['name']:
            op.drop_constraint(fk['name'], 'friend_schedule', type_='foreignkey')

    discord_friends_pk = inspector.get_pk_constraint('discord_friends')
    op.drop_constraint(discord_friends_pk['name'], 'discord_friends', type_='primary')
    op.create_primary_key('discord_friends_pkey', 'discord_friends', ['id', 'friend_code'])

    friends_pk = inspector.get_pk_constraint('friends')
    op.drop_constraint(friends_pk['name'], 'friends', type_='primary')
    op.create_primary_key('friends_pkey', 'friends', ['friend_code'])
    op.create_unique_constraint('uq_friends_friend_code', 'friends', ['friend_code'])

    op.create_foreign_key(
        'friend_schedule_friend_code_fkey',
        'friend_schedule', 'friends', ['friend_code'], ['friend_code'],
        ondelete='CASCADE',
    )
