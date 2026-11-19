"""Alembic environment: uses the application's models and database settings."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.db import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline():
    from backend.settings import settings

    context.configure(
        url=settings.database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connection = config.attributes.get('connection')
    if connection is not None:
        # Called from backend.migrations with an existing connection.
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    from backend.settings import settings

    connectable = engine_from_config(
        {'sqlalchemy.url': settings.database_url.render_as_string(hide_password=False)},
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
