"""Tests for ``regstack theme design``.

Covers --print-only end-to-end (writes the file from --var pairs),
the GUI orchestration in _run_gui (mocked pywebview + serve so no
real desktop session is needed), and the lazy CLI group wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import regstack.wizard.theme_designer.cli as designer_cli
from regstack.cli.__main__ import cli
from regstack.wizard.theme_designer.writer import THEME_FILE

# ---------------------------------------------------------------------------
# Group wiring
# ---------------------------------------------------------------------------


def test_theme_group_lists_design() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["theme", "--help"])
    assert result.exit_code == 0
    assert "design" in result.output


def test_init_help_does_not_load_designer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy import: invoking unrelated subcommands must not pull in
    pywebview/uvicorn via the designer module."""
    import sys

    # Use delitem so monkeypatch restores sys.modules at teardown —
    # otherwise sibling tests holding `import designer_cli` references
    # end up patching a stale module while Click loads a fresh copy.
    for key in (
        "regstack.wizard.theme_designer.cli",
        "regstack.wizard.theme_designer",
    ):
        if key in sys.modules:
            monkeypatch.delitem(sys.modules, key)

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--help"])
    assert result.exit_code == 0
    assert "regstack.wizard.theme_designer" not in sys.modules


# ---------------------------------------------------------------------------
# --print-only
# ---------------------------------------------------------------------------


def test_print_only_writes_file_from_var_pairs(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "theme",
            "design",
            "--print-only",
            "--target",
            str(tmp_path),
            "--var",
            "--rs-accent=#0d9488",
            "--var",
            "--rs-radius=10",
            "--var",
            "dark:--rs-accent=#2dd4bf",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["light_count"] == 2
    assert payload["dark_count"] == 1
    assert (tmp_path / THEME_FILE).exists()
    text = (tmp_path / THEME_FILE).read_text()
    assert "--rs-accent: #0d9488;" in text
    assert "--rs-radius: 10px;" in text
    assert "@media (prefers-color-scheme: dark)" in text


def test_print_only_with_no_vars_writes_defaults(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["theme", "design", "--print-only", "--target", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    text = (tmp_path / THEME_FILE).read_text()
    assert "--rs-accent: #2563eb;" in text  # default accent


def test_print_only_rejects_bad_value(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "theme",
            "design",
            "--print-only",
            "--target",
            str(tmp_path),
            "--var",
            "--rs-accent=not-a-colour",
        ],
    )
    assert result.exit_code == 2
    assert "--rs-accent" in result.output
    assert not (tmp_path / THEME_FILE).exists()


def test_print_only_rejects_malformed_var(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "theme",
            "design",
            "--print-only",
            "--target",
            str(tmp_path),
            "--var",
            "no-equals-sign",
        ],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# GUI orchestration — _run_gui (mocked pywebview + serve)
# ---------------------------------------------------------------------------


def _stub_serve_factory(call_log: list[str]) -> Any:
    async def _stub(server: Any) -> None:
        call_log.append("serve-called")
        await server.settings.shutdown_event.wait()

    return _stub


def test_run_gui_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    call_log: list[str] = []

    def fake_open_window(server: Any) -> None:
        call_log.append(f"opened:{server.url}")
        server.settings.shutdown_event.set()

    monkeypatch.setattr(designer_cli, "serve", _stub_serve_factory(call_log))
    import regstack.wizard.theme_designer.window as window_mod

    monkeypatch.setattr(window_mod, "open_designer_window", fake_open_window)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["theme", "design", "--target", str(tmp_path), "--port", "0"],
    )
    assert result.exit_code == 0, result.output
    assert "Designer URL:" in result.output
    assert any(s.startswith("opened:") for s in call_log)
    assert "serve-called" in call_log


def test_run_gui_window_error_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import regstack.wizard.theme_designer.window as window_mod

    def fake_open_window(server: Any) -> None:
        raise window_mod.DesignerWindowError("no display")

    monkeypatch.setattr(designer_cli, "serve", _stub_serve_factory([]))
    monkeypatch.setattr(window_mod, "open_designer_window", fake_open_window)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["theme", "design", "--target", str(tmp_path), "--port", "0"],
    )
    assert result.exit_code == 1
    assert "no display" in result.output
