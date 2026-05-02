"""Tests for the ``regstack migrate`` CLI command.

Drives the command end-to-end against a fresh tmp_path SQLite file —
no shared state, no mocks of the Alembic layer beyond a single
failure-injection test. Covers:

- fresh upgrade from empty DB to ``head``
- already-at-head no-op
- explicit ``--target`` argument
- the mongo-skip branch (no real mongo connection involved — the
  branch is selected purely from the URL scheme)
- the alembic-failure path (exit code 1, error surfaced)
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from regstack.cli.__main__ import cli


def _write_sqlite_config(tmp_path: Path) -> tuple[Path, Path]:
    sqlite_path = tmp_path / "migrate.db"
    cfg = tmp_path / "regstack.toml"
    cfg.write_text(
        f"""\
app_name = "migrate-test"
base_url = "http://localhost:8000"
database_url = "sqlite+aiosqlite:///{sqlite_path}"

[email]
backend = "console"
from_address = "noreply@example.com"

[sms]
backend = "null"
"""
    )
    return cfg, sqlite_path


def _write_mongo_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "regstack.toml"
    cfg.write_text(
        """\
app_name = "migrate-test"
base_url = "http://localhost:8000"
database_url = "mongodb://example.invalid:27017/regstack"

[email]
backend = "console"
from_address = "noreply@example.com"

[sms]
backend = "null"
"""
    )
    return cfg


@pytest.fixture
def migrate_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Strip ambient REGSTACK_* env vars and provide a JWT secret."""
    for var in list(os.environ):
        if var.startswith("REGSTACK_"):
            monkeypatch.delenv(var, raising=False)
    secret = secrets.token_urlsafe(64)
    monkeypatch.setenv("REGSTACK_JWT_SECRET", secret)
    return secret


def _invoke(cfg: Path, *extra: str) -> Any:
    return CliRunner().invoke(cli, ["migrate", "--config", str(cfg), *extra])


# ---------------------------------------------------------------------------
# SQL backend
# ---------------------------------------------------------------------------


def test_migrate_fresh_db_upgrades_to_head(migrate_env: str, tmp_path: Path) -> None:
    cfg, _ = _write_sqlite_config(tmp_path)
    result = _invoke(cfg)
    assert result.exit_code == 0, result.output
    assert "upgrading" in result.output
    assert "now at" in result.output


def test_migrate_already_at_head_is_noop(migrate_env: str, tmp_path: Path) -> None:
    cfg, _ = _write_sqlite_config(tmp_path)
    first = _invoke(cfg)
    assert first.exit_code == 0, first.output

    second = _invoke(cfg)
    assert second.exit_code == 0, second.output
    assert "already at head" in second.output
    # Did NOT print the "upgrading" line on the second pass.
    assert "upgrading" not in second.output


def test_migrate_explicit_target_revision(migrate_env: str, tmp_path: Path) -> None:
    cfg, _ = _write_sqlite_config(tmp_path)
    result = _invoke(cfg, "--target", "0001")
    assert result.exit_code == 0, result.output
    # First migration is the initial schema; reaching it should land us
    # at exactly that revision.
    assert "0001" in result.output

    # Re-running with --target=head finishes the upgrade.
    after = _invoke(cfg, "--target", "head")
    assert after.exit_code == 0, after.output


def test_migrate_alembic_failure_returns_exit_code_1(
    migrate_env: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = _write_sqlite_config(tmp_path)

    import regstack.backends.sql.migrations as migrations_mod

    def boom(database_url: str, revision: str = "head") -> None:
        raise RuntimeError("alembic blew up")

    monkeypatch.setattr(migrations_mod, "upgrade", boom)

    result = _invoke(cfg)
    assert result.exit_code == 1
    assert "upgrade failed" in result.output
    assert "alembic blew up" in result.output


# ---------------------------------------------------------------------------
# Mongo skip branch
# ---------------------------------------------------------------------------


def test_migrate_mongo_backend_is_silently_skipped(migrate_env: str, tmp_path: Path) -> None:
    """The mongo branch is chosen purely from the URL scheme — no
    real mongo connection is established by the migrate command, so
    this passes whether or not a local mongod is running.
    """
    cfg = _write_mongo_config(tmp_path)
    result = _invoke(cfg)
    assert result.exit_code == 0, result.output
    assert "skip: mongo backends" in result.output
