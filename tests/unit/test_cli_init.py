"""Tests for the ``regstack init`` wizard.

The wizard is interactive (Click prompts) so we drive it via
``CliRunner(input=...)`` and assert on the files it writes. SQLite is
used throughout so these tests need no external services.
"""

from __future__ import annotations

import stat
from pathlib import Path

from click.testing import CliRunner

from regstack.cli.__main__ import cli


def _accept_all(num_prompts: int) -> str:
    """Hit Enter ``num_prompts`` times — every prompt has a default."""
    return "\n" * num_prompts


# Number of prompts on each happy path. If the wizard grows another
# question these counts need updating; the test failure points to it.
_SQLITE_HAPPY_PATH_PROMPTS = 17
_SMTP_PATH_PROMPTS = 17 + 4  # smtp host/port/starttls/user/pass replace 0
_SES_PATH_PROMPTS = 17 + 1
_MONGO_PATH_PROMPTS = 17 + 1
_POSTGRES_PATH_PROMPTS = 17


def test_init_writes_sqlite_config_with_defaults(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--target", str(tmp_path)],
        input=_accept_all(_SQLITE_HAPPY_PATH_PROMPTS),
    )
    assert result.exit_code == 0, result.output

    cfg = tmp_path / "regstack.toml"
    secrets = tmp_path / "regstack.secrets.env"
    assert cfg.exists()
    assert secrets.exists()

    cfg_text = cfg.read_text()
    assert 'app_name = "MyApp"' in cfg_text
    assert 'base_url = "http://localhost:8000"' in cfg_text
    assert "[email]" in cfg_text
    assert 'backend = "console"' in cfg_text
    # SQLite default writes no mongodb_database (it's omitted on non-mongo paths)
    assert "mongodb_database" not in cfg_text

    secrets_text = secrets.read_text()
    assert "REGSTACK_JWT_SECRET=" in secrets_text
    assert "REGSTACK_DATABASE_URL=sqlite+aiosqlite:///./myapp.sqlite" in secrets_text

    # Mode 0600 — secrets file should not be world-readable.
    mode = stat.S_IMODE(secrets.stat().st_mode)
    assert mode == 0o600, oct(mode)

    assert "Wrote " in result.output


def test_init_postgres_backend(tmp_path: Path) -> None:
    runner = CliRunner()
    # 17 prompts, but the database-backend prompt picks postgres → triggers
    # the Postgres URL prompt (which adds 1) but skips the SQLite-path
    # prompt the SQLite branch added. Net = 17.
    # Prompt sequence to reach postgres branch: defaults until the
    # backend prompt (5 defaults), then "postgres", then defaults.
    # We feed: 4x default, "postgres", 12x default.
    inputs = "\n\n\n\n" + "postgres\n" + "\n" * 12
    result = runner.invoke(cli, ["init", "--target", str(tmp_path)], input=inputs)
    assert result.exit_code == 0, result.output

    secrets_text = (tmp_path / "regstack.secrets.env").read_text()
    assert "REGSTACK_DATABASE_URL=postgresql+asyncpg://postgres@localhost/myapp" in secrets_text


def test_init_mongo_backend(tmp_path: Path) -> None:
    runner = CliRunner()
    # 4x default, "mongo", then default host, default db, then 11x default.
    inputs = "\n\n\n\n" + "mongo\n" + "\n\n" + "\n" * 11
    result = runner.invoke(cli, ["init", "--target", str(tmp_path)], input=inputs)
    assert result.exit_code == 0, result.output

    cfg_text = (tmp_path / "regstack.toml").read_text()
    assert 'mongodb_database = "myapp"' in cfg_text

    secrets_text = (tmp_path / "regstack.secrets.env").read_text()
    assert "REGSTACK_DATABASE_URL=mongodb://localhost:27017/myapp" in secrets_text


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    (tmp_path / "regstack.toml").write_text("# existing\n")
    runner = CliRunner()
    # The overwrite confirm prompt defaults to "no" (abort=True).
    # Sending "n\n" rejects the prompt -> Click aborts with exit 1.
    result = runner.invoke(cli, ["init", "--target", str(tmp_path)], input="n\n")
    assert result.exit_code == 1
    assert "Overwrite?" in result.output


def test_init_force_overwrites(tmp_path: Path) -> None:
    (tmp_path / "regstack.toml").write_text("# existing\n")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--target", str(tmp_path), "--force"],
        input=_accept_all(_SQLITE_HAPPY_PATH_PROMPTS),
    )
    assert result.exit_code == 0, result.output
    cfg_text = (tmp_path / "regstack.toml").read_text()
    assert "# existing" not in cfg_text
    assert 'app_name = "MyApp"' in cfg_text


def test_init_smtp_backend_records_smtp_settings(tmp_path: Path) -> None:
    """Pick SMTP at the email prompt → wizard asks for host/port/starttls/user/pass."""
    # Sequence: app/base/cookie/proxy (4 defaults), backend default (sqlite),
    # sqlite path default, jwt/jwt-ttl/transport (3 defaults),
    # email backend = "smtp", from-address default, from-name default,
    # smtp host = "mail.example.com", smtp port default (587),
    # smtp starttls default (yes), smtp user = "u", smtp pass = "p",
    # then SMS no, admin no, ui no, register yes, verify no.
    inputs = (
        "\n" * 4  # app, base, cookie, proxy
        + "\n"  # backend = sqlite
        + "\n"  # sqlite path
        + "\n\n\n"  # jwt secret auto, jwt ttl, transport
        + "smtp\n"  # email backend
        + "\n\n"  # from-address, from-name
        + "mail.example.com\n"  # smtp host
        + "\n\n"  # smtp port, starttls
        + "u\np\n"  # smtp user, pass
        + "\n\n\n\n\n"  # sms, admin, ui, register, verify
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--target", str(tmp_path)], input=inputs)
    assert result.exit_code == 0, result.output

    cfg_text = (tmp_path / "regstack.toml").read_text()
    assert 'smtp_host = "mail.example.com"' in cfg_text
    assert "smtp_port = 587" in cfg_text
    assert "smtp_starttls = true" in cfg_text
    assert 'smtp_username = "u"' in cfg_text

    secrets_text = (tmp_path / "regstack.secrets.env").read_text()
    assert "REGSTACK_EMAIL__SMTP_PASSWORD=p" in secrets_text
