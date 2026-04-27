"""Click-runner smoke tests for the regstack CLI commands."""

from __future__ import annotations

import os
from pathlib import Path

from click.testing import CliRunner

from regstack.cli.__main__ import cli


def _write_config(tmp_path: Path, *, db_name: str, jwt_secret: str) -> Path:
    cfg = tmp_path / "regstack.toml"
    cfg.write_text(
        f"""\
app_name = "doctor-test"
base_url = "http://localhost:8000"
database_url = "mongodb://localhost:27017/{db_name}"
mongodb_database = "{db_name}"

jwt_ttl_seconds = 7200
require_verification = false
allow_registration = true
enable_password_reset = true

[email]
backend = "console"
from_address = "test@example.com"
from_name = "doctor-test"

[sms]
backend = "null"
"""
    )
    secrets_env = tmp_path / "regstack.secrets.env"
    secrets_env.write_text(
        f"REGSTACK_JWT_SECRET={jwt_secret}\n"
        f"REGSTACK_DATABASE_URL=mongodb://localhost:27017/{db_name}\n"
    )
    return cfg


def test_root_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "create-admin" in result.output
    assert "doctor" in result.output
    assert "init" in result.output


def test_create_admin_help() -> None:
    result = CliRunner().invoke(cli, ["create-admin", "--help"])
    assert result.exit_code == 0
    assert "--email" in result.output


def test_doctor_help() -> None:
    result = CliRunner().invoke(cli, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "--check-dns" in result.output


def test_create_admin_short_password_rejected(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["create-admin", "--email", "a@example.com", "--password", "short"])
    # UsageError → exit code 2.
    assert result.exit_code == 2
    assert "8 characters" in result.output


def test_doctor_runs_against_local_mongo(
    tmp_path: Path, jwt_secret: str, monkeypatch
) -> None:
    db_name = f"regstack_doctor_test_{__import__('secrets').token_hex(4)}"
    """End-to-end smoke for `regstack doctor` against the live local Mongo.

    Skips network-dependent checks (DNS, real email) — exercises the
    config + connectivity + index + email-factory paths and asserts
    the index check correctly flips from red to green after install_indexes.
    """
    cfg_path = _write_config(tmp_path, db_name=db_name, jwt_secret=jwt_secret)

    monkeypatch.setenv("REGSTACK_CONFIG", str(cfg_path))
    # The secrets.env we wrote lives in tmp_path, but the loader searches cwd
    # for it. Promote secrets directly to env so the in-process subcommand
    # invocations don't need to chdir.
    monkeypatch.setenv("REGSTACK_JWT_SECRET", jwt_secret)
    monkeypatch.setenv(
        "REGSTACK_DATABASE_URL", f"mongodb://localhost:27017/{db_name}"
    )
    for var in list(os.environ):
        if var.startswith("REGSTACK_") and var not in {
            "REGSTACK_CONFIG",
            "REGSTACK_JWT_SECRET",
            "REGSTACK_DATABASE_URL",
        }:
            monkeypatch.delenv(var, raising=False)

    runner = CliRunner()

    # Pre-install: doctor reports schema missing.
    result = runner.invoke(cli, ["doctor", "--config", str(cfg_path)])
    assert "backend" in result.output
    assert "email backend" in result.output
    assert "schema" in result.output
    assert "missing" in result.output
    assert result.exit_code >= 1

    # Install schema via the live façade, then re-run doctor — green.
    import asyncio

    from regstack.cli._runtime import open_regstack

    async def _install() -> None:
        async with open_regstack(cfg_path) as rs:
            await rs.install_schema()

    asyncio.run(_install())

    result = runner.invoke(cli, ["doctor", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "core indexes present" in result.output

    # Drop the test DB so we don't leak.
    from regstack.config.schema import RegStackConfig
    from regstack.backends.mongo import make_client

    cfg = RegStackConfig.load(toml_path=cfg_path)

    async def _drop() -> None:
        client = make_client(cfg)
        await client.drop_database(cfg.mongodb_database)
        await client.aclose()

    asyncio.run(_drop())
