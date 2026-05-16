"""Pin the NULL-guard on migration 0002's downgrade.

Pre-fix, the downgrade silently succeeded on SQLite even when
``users.hashed_password`` contained NULLs from OAuth-only signups,
leaving the deployed schema inconsistent with its NOT NULL constraint.

Flagged as I-4 in the 2026-05-15 / 2026-05-16 security reviews.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from regstack.backends.sql.migrations import _build_config, upgrade


@pytest.fixture
def backend_kind() -> str:
    """SQL-backend concern; SQLite gives us a fresh per-test DB without
    needing live Postgres."""
    return "sqlite"


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'mig0002.sqlite'}"


def _sync_url(url: str) -> str:
    """Alembic's downgrade command takes a sync URL."""
    return url.replace("+aiosqlite", "")


def _insert_user(url: str, *, hashed_password: str | None) -> None:
    engine = create_engine(_sync_url(url), future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, full_name, "
                    "is_active, is_verified, is_superuser, is_mfa_enabled, "
                    "created_at, updated_at) "
                    "VALUES (:id, :email, :hp, :fn, :ia, :iv, :is, :imfa, "
                    ":ca, :ua)"
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "email": "oauth-only@example.com",
                    "hp": hashed_password,
                    "fn": None,
                    "ia": True,
                    "iv": True,
                    "is": False,
                    "imfa": False,
                    "ca": "2026-01-01T00:00:00+00:00",
                    "ua": "2026-01-01T00:00:00+00:00",
                },
            )
    finally:
        engine.dispose()


def test_downgrade_blocked_when_oauth_only_users_present(sqlite_url: str) -> None:
    """The exact regression the I-4 guard exists to prevent."""
    upgrade(sqlite_url)
    _insert_user(sqlite_url, hashed_password=None)

    cfg = _build_config(sqlite_url)
    with pytest.raises(RuntimeError, match="OAuth-only user"):
        command.downgrade(cfg, "0001")


def test_downgrade_proceeds_when_no_null_passwords(sqlite_url: str) -> None:
    """Happy path: users all have passwords, downgrade is allowed."""
    upgrade(sqlite_url)
    _insert_user(sqlite_url, hashed_password="$argon2id$..." + "x" * 80)

    cfg = _build_config(sqlite_url)
    # Must not raise.
    command.downgrade(cfg, "0001")


def test_downgrade_on_empty_db_proceeds(sqlite_url: str) -> None:
    """Empty users table → no NULLs → downgrade allowed."""
    upgrade(sqlite_url)
    cfg = _build_config(sqlite_url)
    command.downgrade(cfg, "0001")
