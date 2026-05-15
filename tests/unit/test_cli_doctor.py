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


def test_doctor_send_test_email_falls_back_to_app_name_when_from_name_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When ``email.from_name`` is unset, ``--send-test-email`` must still
    produce a valid From: header by falling back to ``app_name`` — matching
    the same fallback ``MailComposer`` does for the templated emails.

    Before the fix, ``_send_test_email`` passed ``config.email.from_name``
    (now ``Optional[str]``) straight into ``EmailMessage.from_name`` (typed
    ``str``), producing a ``None <addr>`` From: header.
    """
    from regstack.cli import doctor as doctor_mod
    from regstack.config.schema import EmailConfig, RegStackConfig
    from regstack.email.base import EmailMessage, EmailService

    captured: list[EmailMessage] = []

    class _Capturing(EmailService):
        async def send(self, message: EmailMessage) -> None:
            captured.append(message)

    monkeypatch.setattr(doctor_mod, "build_email_service", lambda _cfg: _Capturing())

    cfg = RegStackConfig.load(
        toml_path=Path("/dev/null"),
        secrets_env_path=Path("/dev/null"),
        jwt_secret=secrets.token_urlsafe(64),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor.db'}",
        app_name="acme-app",
        email=EmailConfig(backend="console", from_address="noreply@acme.example"),
    )
    assert cfg.email.from_name is None  # precondition: unset

    result = asyncio.run(doctor_mod._send_test_email(cfg, "probe@example.com"))
    assert result.ok, result.detail
    assert len(captured) == 1
    assert captured[0].from_name == "acme-app"


def test_doctor_send_test_email_respects_explicit_from_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit ``from_name`` must override the ``app_name`` fallback."""
    from regstack.cli import doctor as doctor_mod
    from regstack.config.schema import EmailConfig, RegStackConfig
    from regstack.email.base import EmailMessage, EmailService

    captured: list[EmailMessage] = []

    class _Capturing(EmailService):
        async def send(self, message: EmailMessage) -> None:
            captured.append(message)

    monkeypatch.setattr(doctor_mod, "build_email_service", lambda _cfg: _Capturing())

    cfg = RegStackConfig.load(
        toml_path=Path("/dev/null"),
        secrets_env_path=Path("/dev/null"),
        jwt_secret=secrets.token_urlsafe(64),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor.db'}",
        app_name="acme-app",
        email=EmailConfig(
            backend="console",
            from_address="noreply@acme.example",
            from_name="Acme Customer Service",
        ),
    )

    result = asyncio.run(doctor_mod._send_test_email(cfg, "probe@example.com"))
    assert result.ok, result.detail
    assert captured[0].from_name == "Acme Customer Service"


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
