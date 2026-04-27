"""Tests for the bundled Alembic migration story.

Pinned to the SQLite parametrization because Alembic is a SQL-backend
concern; Mongo has no equivalent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from regstack.backends.sql.migrations import (
    current,
    head_revision,
    revision_history,
    upgrade,
)


@pytest.fixture
def backend_kind() -> str:
    """Pin this module to sqlite so the parametrized fixture doesn't
    schedule mongo runs (Alembic isn't relevant to Mongo).
    """
    return "sqlite"


@pytest.fixture
def fresh_sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'fresh.sqlite'}"


def test_head_is_first_revision() -> None:
    history = list(revision_history())
    assert history, "no migrations bundled"
    assert head_revision() == history[-1], (
        "head_revision() must point at the most recent revision in the chain"
    )


def test_upgrade_then_current_returns_head(fresh_sqlite_url: str) -> None:
    assert current(fresh_sqlite_url) is None  # fresh DB has no alembic_version

    upgrade(fresh_sqlite_url)

    assert current(fresh_sqlite_url) == head_revision()


def test_upgrade_is_idempotent(fresh_sqlite_url: str) -> None:
    upgrade(fresh_sqlite_url)
    head = current(fresh_sqlite_url)

    # Re-running on a head DB is a no-op — must not raise, must not move.
    upgrade(fresh_sqlite_url)
    upgrade(fresh_sqlite_url)

    assert current(fresh_sqlite_url) == head


def test_no_autogen_drift(fresh_sqlite_url: str) -> None:
    """If `schema.py` and the bundled migrations diverge, alembic
    autogenerate would emit pending operations. This test runs autogen
    against a head-of-line DB and asserts the diff is empty.

    Catches the common bug where someone adds a column to schema.py
    and forgets to write the matching migration.
    """
    upgrade(fresh_sqlite_url)

    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    from regstack.backends.sql.schema import metadata

    sync_url = fresh_sqlite_url.replace("+aiosqlite", "")
    engine = create_engine(sync_url, future=True)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                connection=conn,
                opts={"render_as_batch": True, "compare_type": True},
            )
            diff = compare_metadata(ctx, metadata)
    finally:
        engine.dispose()

    assert diff == [], f"schema.py drift from bundled migrations: {diff}"
