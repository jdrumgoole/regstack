"""Alembic environment for regstack's SQL backends.

Loaded by ``alembic`` via ``script_location`` set to this directory.
The MetaData lives in :mod:`regstack.backends.sql.schema`; the
``database_url`` comes from the Config object built in
:mod:`regstack.backends.sql.migrations`.

Both online (live engine) and offline (SQL emit-only) modes are
implemented because Alembic's autogenerate command uses the offline
machinery to compare metadata against itself.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from regstack.backends.sql.schema import metadata

config = context.config
target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # `render_as_batch` lets SQLite emulate ALTER TABLE ops alembic
        # emits for column adds/drops. Postgres ignores it.
        render_as_batch=url is not None and url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # alembic ships a sync Engine factory; we use the SYNC driver
    # variants here (sqlite vs sqlite+aiosqlite, postgresql vs
    # postgresql+asyncpg) so DDL doesn't require an event loop.
    url = config.get_main_option("sqlalchemy.url") or ""
    sync_url = (
        url.replace("+aiosqlite", "")
        .replace("postgresql+asyncpg", "postgresql+psycopg")
        .replace("postgres+asyncpg", "postgresql+psycopg")
    )
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = sync_url

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=sync_url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
