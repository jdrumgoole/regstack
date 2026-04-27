"""Alembic environment for regstack's SQL backends.

Loaded by ``alembic`` via ``script_location`` set to this directory.
The MetaData lives in :mod:`regstack.backends.sql.schema`; the
``database_url`` comes from the Config object built in
:mod:`regstack.backends.sql.migrations`.

Async strategy: we drive Alembic through SQLAlchemy 2's async engine
(aiosqlite / asyncpg) and use ``connection.run_sync`` to call into
Alembic's sync migration machinery. This means hosts only need the
async drivers — no psycopg required for Postgres migrations.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from regstack.backends.sql.schema import metadata

config = context.config
target_metadata = metadata


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _do_run_migrations(connection) -> None:
    url = config.get_main_option("sqlalchemy.url") or ""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite can't ALTER columns in place — render_as_batch tells
        # Alembic to emit copy-and-rename ops where needed.
        render_as_batch=_is_sqlite(url),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or ""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(url),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(section, prefix="sqlalchemy.")
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
