"""Pin the 0.8.x CLI flag-unification: ``--config`` is canonical;
``--target`` is a deprecated alias on config-writing commands;
``--config`` accepts either a file path or a directory."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from regstack.cli.__main__ import cli
from regstack.cli._paths import resolve_target_dir, resolve_toml_path

# --------------------------------------------------------------------------
# resolve_target_dir
# --------------------------------------------------------------------------


def test_resolve_target_dir_prefers_config(tmp_path: Path) -> None:
    out = resolve_target_dir(config=tmp_path, target=None)
    assert out == tmp_path.resolve()


def test_resolve_target_dir_accepts_file_path(tmp_path: Path) -> None:
    toml = tmp_path / "regstack.toml"
    toml.write_text("")
    out = resolve_target_dir(config=toml, target=None)
    assert out == tmp_path.resolve()


def test_resolve_target_dir_accepts_missing_file_with_toml_suffix(tmp_path: Path) -> None:
    """A bare path like ``./conf/regstack.toml`` that doesn't exist yet
    still resolves to the parent directory — ``init`` may need to create
    the file there."""
    toml = tmp_path / "subdir" / "regstack.toml"
    out = resolve_target_dir(config=toml, target=None)
    assert out == (tmp_path / "subdir").resolve()


def test_resolve_target_dir_target_alias_emits_no_error(tmp_path: Path) -> None:
    out = resolve_target_dir(config=None, target=tmp_path)
    assert out == tmp_path.resolve()


def test_resolve_target_dir_rejects_both_flags(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="mutually exclusive"):
        resolve_target_dir(config=tmp_path, target=tmp_path)


def test_resolve_target_dir_defaults_to_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = resolve_target_dir(config=None, target=None)
    assert out == tmp_path.resolve()


# --------------------------------------------------------------------------
# resolve_toml_path (read-only commands)
# --------------------------------------------------------------------------


def test_resolve_toml_path_returns_none_when_unset() -> None:
    assert resolve_toml_path(None) is None


def test_resolve_toml_path_accepts_file(tmp_path: Path) -> None:
    toml = tmp_path / "regstack.toml"
    toml.write_text("")
    assert resolve_toml_path(toml) == toml.resolve()


def test_resolve_toml_path_accepts_dir(tmp_path: Path) -> None:
    out = resolve_toml_path(tmp_path)
    assert out == tmp_path.resolve() / "regstack.toml"


# --------------------------------------------------------------------------
# init: --config works AND --target still works with deprecation warning
# --------------------------------------------------------------------------


_SQLITE_HAPPY_PATH_PROMPTS = 15


def _accept_all(num_prompts: int) -> str:
    return "\n" * num_prompts


def test_init_accepts_config_flag(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--config", str(tmp_path)],
        input=_accept_all(_SQLITE_HAPPY_PATH_PROMPTS),
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "regstack.toml").exists()


def test_init_accepts_config_as_file_path(tmp_path: Path) -> None:
    runner = CliRunner()
    toml = tmp_path / "regstack.toml"
    result = runner.invoke(
        cli,
        ["init", "--config", str(toml)],
        input=_accept_all(_SQLITE_HAPPY_PATH_PROMPTS),
    )
    assert result.exit_code == 0, result.output
    assert toml.exists()


def test_init_target_still_works(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--target", str(tmp_path)],
        input=_accept_all(_SQLITE_HAPPY_PATH_PROMPTS),
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "regstack.toml").exists()


def test_init_target_emits_deprecation(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--target", str(tmp_path)],
        input=_accept_all(_SQLITE_HAPPY_PATH_PROMPTS),
    )
    assert result.exit_code == 0, result.output
    # CliRunner merges stderr into output by default in Click 8.x.
    assert "Deprecation" in result.output
    assert "--target is deprecated" in result.output


def test_init_rejects_both_flags(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--config", str(tmp_path), "--target", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


# --------------------------------------------------------------------------
# Wizard --headless / --dry-run / --print-only deprecation
# --------------------------------------------------------------------------


def test_theme_design_dry_run_does_not_write(tmp_path: Path) -> None:
    """``--dry-run`` validates and emits a JSON summary but leaves no files."""
    import json

    from regstack.wizard.theme_designer.writer import THEME_FILE

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "theme",
            "design",
            "--dry-run",
            "--config",
            str(tmp_path),
            "--var",
            "--rs-accent=#0d9488",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["light_count"] == 1
    assert not (tmp_path / THEME_FILE).exists()


def test_theme_design_headless_writes_file(tmp_path: Path) -> None:
    import json

    from regstack.wizard.theme_designer.writer import THEME_FILE

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "theme",
            "design",
            "--headless",
            "--config",
            str(tmp_path),
            "--var",
            "--rs-accent=#0d9488",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is False
    assert (tmp_path / THEME_FILE).exists()


def test_theme_design_print_only_deprecation_warning(tmp_path: Path) -> None:
    """``--print-only`` is the deprecated alias for ``--headless``. Using it
    must emit a deprecation warning, but still write the file (preserve
    behaviour for existing scripts)."""
    from regstack.wizard.theme_designer.writer import THEME_FILE

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "theme",
            "design",
            "--print-only",
            "--config",
            str(tmp_path),
            "--var",
            "--rs-accent=#0d9488",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "--print-only is deprecated" in result.output
    assert (tmp_path / THEME_FILE).exists()


def test_oauth_dry_run_does_not_write(tmp_path: Path) -> None:
    import json

    from regstack.wizard.oauth_google.writer import CONFIG_FILE, SECRETS_FILE

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "oauth",
            "setup",
            "--dry-run",
            "--config",
            str(tmp_path),
            "--client-id",
            "12345-abc.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-secretvalue1234",
            "--base-url",
            "http://localhost:8000",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert not (tmp_path / CONFIG_FILE).exists()
    assert not (tmp_path / SECRETS_FILE).exists()


def test_ses_dry_run_does_not_write(tmp_path: Path) -> None:
    import json

    from regstack.wizard.ses.writer import CONFIG_FILE, SECRETS_FILE

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ses",
            "setup",
            "--dry-run",
            "--config",
            str(tmp_path),
            "--region",
            "eu-west-1",
            "--from-address",
            "noreply@example.com",
            "--credential-source",
            "chain",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert not (tmp_path / CONFIG_FILE).exists()
    assert not (tmp_path / SECRETS_FILE).exists()


def test_ses_headless_writes(tmp_path: Path) -> None:
    import json

    from regstack.wizard.ses.writer import CONFIG_FILE

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ses",
            "setup",
            "--headless",
            "--config",
            str(tmp_path),
            "--region",
            "eu-west-1",
            "--from-address",
            "noreply@example.com",
            "--credential-source",
            "chain",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is False
    assert (tmp_path / CONFIG_FILE).exists()
