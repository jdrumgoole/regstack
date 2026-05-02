"""Tests for ``regstack oauth setup``.

Two surfaces are covered here:

- ``--print-only`` mode end-to-end (writes regstack.toml + secrets,
  rejects bad payloads, chmod 0600).
- The GUI orchestration in ``_run_gui`` — drained of pywebview by
  monkeypatching ``open_wizard_window`` and the uvicorn ``serve``
  coroutine, so the CLI's lifecycle wiring (server thread spin-up,
  shutdown-event signalling, WizardWindowError exit code) gets
  exercised without a real desktop session.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import regstack.wizard.oauth_google.cli as wizard_cli
from regstack.cli.__main__ import cli
from regstack.wizard.oauth_google.writer import (
    CONFIG_FILE,
    SECRETS_ENV_KEY,
    SECRETS_FILE,
)


def _run(tmp_path: Path, *extra: str) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "oauth",
            "setup",
            "--print-only",
            "--target",
            str(tmp_path),
            "--client-id",
            "12345-abc.apps.googleusercontent.com",
            "--client-secret",
            "GOCSPX-secretvalue1234",
            "--base-url",
            "http://localhost:8000",
            *extra,
        ],
    )
    return result.exit_code, result.output


def test_print_only_writes_files_and_emits_json(tmp_path: Path) -> None:
    code, out = _run(tmp_path)
    assert code == 0, out
    payload = json.loads(out)
    assert payload["config_diff"] == "added [oauth] table"
    assert payload["secrets_diff"].endswith(SECRETS_ENV_KEY)
    cfg = (tmp_path / CONFIG_FILE).read_text()
    assert "enable_oauth = true" in cfg
    assert "12345-abc.apps.googleusercontent.com" in cfg
    secrets = (tmp_path / SECRETS_FILE).read_text()
    assert f"{SECRETS_ENV_KEY}=GOCSPX-secretvalue1234" in secrets


def test_print_only_chmods_secrets_file(tmp_path: Path) -> None:
    code, out = _run(tmp_path)
    assert code == 0, out
    mode = stat.S_IMODE((tmp_path / SECRETS_FILE).stat().st_mode)
    assert mode == 0o600


def test_print_only_rejects_bad_client_id(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "oauth",
            "setup",
            "--print-only",
            "--target",
            str(tmp_path),
            "--client-id",
            "not-a-google-id",
            "--client-secret",
            "GOCSPX-secretvalue1234",
        ],
    )
    assert result.exit_code == 2
    assert "client_id" in result.output
    # Nothing got written.
    assert not (tmp_path / CONFIG_FILE).exists()


def test_oauth_group_lists_setup() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["oauth", "--help"])
    assert result.exit_code == 0
    assert "setup" in result.output


# ---------------------------------------------------------------------------
# GUI orchestration — _run_gui
# ---------------------------------------------------------------------------


def _stub_serve_factory(call_log: list[str]) -> Any:
    """Build a coroutine that records its call and returns immediately.

    Used in place of :func:`regstack.wizard.oauth_google.server.serve`
    so the test's background thread spins up, signals the shutdown
    event itself (via the stubbed ``open_wizard_window``), and exits
    without ever binding a port.
    """

    async def _stub(server: Any) -> None:
        call_log.append("serve-called")
        await server.settings.shutdown_event.wait()

    return _stub


def test_run_gui_happy_path_uses_make_wizard_server_and_joins_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Window closes normally → finally block tears the server down."""
    call_log: list[str] = []

    def fake_open_window(server: Any) -> None:
        call_log.append(f"opened:{server.url}")
        # Real pywebview returns when the user closes the window.
        # Mirror that by signalling the shutdown event.
        server.settings.shutdown_event.set()

    monkeypatch.setattr(wizard_cli, "serve", _stub_serve_factory(call_log))

    # `open_wizard_window` is imported *inside* _run_gui from the
    # window module, not from cli — patch the source.
    import regstack.wizard.oauth_google.window as window_mod

    monkeypatch.setattr(window_mod, "open_wizard_window", fake_open_window)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["oauth", "setup", "--target", str(tmp_path), "--port", "0"],
    )
    assert result.exit_code == 0, result.output
    assert "Wizard URL:" in result.output
    assert any(s.startswith("opened:") for s in call_log)
    assert "serve-called" in call_log


def test_run_gui_window_error_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A WizardWindowError from pywebview surfaces as exit code 1."""
    call_log: list[str] = []

    import regstack.wizard.oauth_google.window as window_mod

    def fake_open_window(server: Any) -> None:
        raise window_mod.WizardWindowError("no display")

    monkeypatch.setattr(wizard_cli, "serve", _stub_serve_factory(call_log))
    monkeypatch.setattr(window_mod, "open_wizard_window", fake_open_window)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["oauth", "setup", "--target", str(tmp_path), "--port", "0"],
    )
    assert result.exit_code == 1
    assert "no display" in result.output


def test_run_gui_passes_existing_base_url_into_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If regstack.toml already declares a base_url, the wizard server
    is built with that value pre-populated so step 2 shows it."""
    (tmp_path / CONFIG_FILE).write_text('app_name = "X"\nbase_url = "https://prod.example.com"\n')

    captured: dict[str, Any] = {}

    def fake_open_window(server: Any) -> None:
        captured["existing_base_url"] = server.settings.existing_base_url
        server.settings.shutdown_event.set()

    monkeypatch.setattr(wizard_cli, "serve", _stub_serve_factory([]))
    import regstack.wizard.oauth_google.window as window_mod

    monkeypatch.setattr(window_mod, "open_wizard_window", fake_open_window)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["oauth", "setup", "--target", str(tmp_path), "--port", "0"],
    )
    assert result.exit_code == 0, result.output
    assert captured["existing_base_url"] == "https://prod.example.com"


# ---------------------------------------------------------------------------
# _existing_base_url
# ---------------------------------------------------------------------------


def test_existing_base_url_returns_none_when_no_file(tmp_path: Path) -> None:
    assert wizard_cli._existing_base_url(tmp_path / CONFIG_FILE) is None


def test_existing_base_url_returns_value(tmp_path: Path) -> None:
    p = tmp_path / CONFIG_FILE
    p.write_text('base_url = "https://app.example.com"\n')
    assert wizard_cli._existing_base_url(p) == "https://app.example.com"


def test_existing_base_url_handles_corrupt_toml(tmp_path: Path) -> None:
    p = tmp_path / CONFIG_FILE
    p.write_text("this = is [ not valid toml")
    assert wizard_cli._existing_base_url(p) is None


def test_existing_base_url_returns_none_when_field_missing(tmp_path: Path) -> None:
    p = tmp_path / CONFIG_FILE
    p.write_text('app_name = "X"\n')
    assert wizard_cli._existing_base_url(p) is None
