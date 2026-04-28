"""Programmatic Alembic API for the SQL backends.

regstack ships its migration environment inside the package so hosts
don't need an `alembic.ini` on disk. Use these functions directly:

    from regstack.backends.sql.migrations import upgrade, current

    await upgrade("sqlite+aiosqlite:///./regstack.db")     # to head
    await upgrade("postgresql+asyncpg://...", "0001")       # to a specific revision

The `regstack migrate` CLI command and `SqlBackend.install_schema()`
both call into here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from collections.abc import Iterable

# Bundled alembic env lives next to this file.
_MIGRATIONS_DIR = Path(__file__).parent
_VERSIONS_DIR = _MIGRATIONS_DIR / "versions"


def _build_config(database_url: str) -> Config:
    """Build an in-memory Alembic Config that points at the bundled env.

    No ``alembic.ini`` on disk — every setting is set programmatically so
    a host using regstack as a library doesn't need to ship one.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.set_main_option("version_locations", str(_VERSIONS_DIR))
    # Alembic 1.13+ deprecates implicit comma/space splitting on
    # version_locations; pin the separator explicitly.
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    """Synchronously run ``alembic upgrade <revision>``.

    Idempotent — running on a DB that's already at the target is a no-op.
    Safe to call from app startup. The function is sync because alembic
    drives its own engine internally; for async-only call sites use
    :func:`upgrade_async` or call this through ``asyncio.to_thread``.
    """
    cfg = _build_config(database_url)
    alembic_command.upgrade(cfg, revision)


async def upgrade_async(database_url: str, revision: str = "head") -> None:
    """Async-friendly wrapper around :func:`upgrade`.

    Uses ``asyncio.to_thread`` so we don't block the event loop on
    DDL — useful when called from inside a FastAPI ``lifespan`` startup.
    """
    import asyncio

    await asyncio.to_thread(upgrade, database_url, revision)


def current(database_url: str) -> str | None:
    """Return the current revision recorded in alembic_version, or None
    if the table doesn't exist (i.e. fresh DB).

    Synchronous; do NOT call from inside a running event loop —
    use :func:`current_async` instead.
    """
    import asyncio

    return asyncio.run(_current_async_impl(database_url))


async def current_async(database_url: str) -> str | None:
    """Async variant of :func:`current` — safe to call from inside an
    already-running event loop (e.g. ``regstack doctor``)."""
    return await _current_async_impl(database_url)


async def _current_async_impl(database_url: str) -> str | None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_current_sync)
    finally:
        await engine.dispose()


def _current_sync(connection) -> str | None:
    from alembic.runtime.migration import MigrationContext

    return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str:
    """The bundled head revision — useful for assertions in tests and
    in `regstack doctor` to detect drift between deployed schema and
    package version.
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_build_config("sqlite://"))
    return script.get_current_head() or ""


def revision_history() -> Iterable[str]:
    """Iterate over revisions in oldest-first order."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_build_config("sqlite://"))
    return [rev.revision for rev in script.walk_revisions(base="base", head="heads")][::-1]


__all__ = [
    "current",
    "head_revision",
    "revision_history",
    "upgrade",
    "upgrade_async",
]
