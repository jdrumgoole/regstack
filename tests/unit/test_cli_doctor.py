"""Tests for the ``regstack doctor`` CLI command.

Targets the SQLite branch (no infrastructure required) and exercises:

- the JWT-secret quality check (missing / too short / present)
- the backend ``ping`` path
- the schema check before and after ``install_schema``
- the email-factory check
- the ``--send-test-email`` path against the console backend
- the ``--check-dns`` path with a domain we expect to fail
"""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path

import pytest
from click.testing import CliRunner

from regstack.cli.__main__ import cli
from regstack.cli._runtime import open_regstack


def _write_sqlite_config(
    tmp_path: Path,
    *,
    from_address: str = "noreply@example.com",
) -> tuple[Path, Path]:
    sqlite_path = tmp_path / "doctor.db"
    cfg = tmp_path / "regstack.toml"
    cfg.write_text(
        f"""\
app_name = "doctor-test"
base_url = "http://localhost:8000"
database_url = "sqlite+aiosqlite:///{sqlite_path}"

jwt_ttl_seconds = 7200
require_verification = false
allow_registration = true

[email]
backend = "console"
from_address = "{from_address}"
from_name = "doctor-test"

[sms]
backend = "null"
"""
    )
    return cfg, sqlite_path


@pytest.fixture
def doctor_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, str]:
    """Strip ambient REGSTACK_* vars, then set a known JWT secret + DB URL.

    Returns ``(config_path, jwt_secret)``.
    """
    for var in list(os.environ):
        if var.startswith("REGSTACK_"):
            monkeypatch.delenv(var, raising=False)
    secret = secrets.token_urlsafe(64)
    cfg_path, sqlite_path = _write_sqlite_config(tmp_path)
    monkeypatch.setenv("REGSTACK_JWT_SECRET", secret)
    monkeypatch.setenv("REGSTACK_DATABASE_URL", f"sqlite+aiosqlite:///{sqlite_path}")
    return cfg_path, secret


def test_doctor_reports_schema_missing_then_present(
    doctor_env: tuple[Path, str],
) -> None:
    """Pre-install: doctor reports schema missing. Post-install: green."""
    cfg_path, _ = doctor_env
    runner = CliRunner()

    # Pre-install: schema check fails (alembic_version table missing).
    result = runner.invoke(cli, ["doctor", "--config", str(cfg_path)])
    assert result.exit_code >= 1
    assert "schema" in result.output
    assert "alembic_version table missing" in result.output
    assert "jwt secret" in result.output
    assert "email backend" in result.output

    # Install schema, then re-run.
    async def _install() -> None:
        async with open_regstack(cfg_path) as rs:
            await rs.install_schema()

    asyncio.run(_install())

    result = runner.invoke(cli, ["doctor", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "at head" in result.output


def test_doctor_flags_short_jwt_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in list(os.environ):
        if var.startswith("REGSTACK_"):
            monkeypatch.delenv(var, raising=False)
    cfg_path, sqlite_path = _write_sqlite_config(tmp_path)
    monkeypatch.setenv("REGSTACK_JWT_SECRET", "too-short")
    monkeypatch.setenv("REGSTACK_DATABASE_URL", f"sqlite+aiosqlite:///{sqlite_path}")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(cfg_path)])
    assert result.exit_code >= 1
    assert "jwt secret" in result.output
    assert "too short" in result.output


def test_doctor_send_test_email_via_console(doctor_env: tuple[Path, str]) -> None:
    """--send-test-email succeeds against the console backend (it just prints)."""
    cfg_path, _ = doctor_env

    # Bring schema up so the schema check passes — otherwise the failed
    # schema check makes doctor exit non-zero and we can't observe the
    # send-test-email check independently.
    async def _install() -> None:
        async with open_regstack(cfg_path) as rs:
            await rs.install_schema()

    asyncio.run(_install())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "doctor",
            "--config",
            str(cfg_path),
            "--send-test-email",
            "probe@example.com",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "email send" in result.output
    assert "probe@example.com" in result.output


def test_doctor_check_dns_runs_lookups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--check-dns runs SPF/DKIM/MX lookups for the sender domain.

    We use ``example.com`` as the sender — it's RFC 2606 reserved so it
    accepts no traffic, which is fine here: we only care that doctor
    actually invokes the dig probes (the labels appear in output) and
    handles whatever response comes back without crashing.
    """
    for var in list(os.environ):
        if var.startswith("REGSTACK_"):
            monkeypatch.delenv(var, raising=False)
    cfg_path, sqlite_path = _write_sqlite_config(tmp_path, from_address="probe@example.com")
    monkeypatch.setenv("REGSTACK_JWT_SECRET", secrets.token_urlsafe(64))
    monkeypatch.setenv("REGSTACK_DATABASE_URL", f"sqlite+aiosqlite:///{sqlite_path}")

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--config", str(cfg_path), "--check-dns"])
    # The schema check fails (we didn't install it), so exit code ≥ 1 —
    # we just care that the DNS check labels are present in output.
    assert "dns mx" in result.output
    assert "dns spf" in result.output
    assert "dns dmarc" in result.output
