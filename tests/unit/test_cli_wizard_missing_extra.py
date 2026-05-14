"""Wizard subcommands are gated behind the optional ``wizard`` extra.

When a host installs `regstack` without `regstack[wizard]` and runs
`regstack oauth setup` or `regstack theme design`, they should get a
one-line install hint and a non-zero exit code — not an ImportError
traceback from deep inside the wizard subtree.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator

import pytest
from click.testing import CliRunner

from regstack.cli.__main__ import cli


@pytest.fixture
def block_wizard_imports(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make `from regstack.wizard.oauth_google.cli import ...` and the
    theme-designer equivalent raise ImportError. Simulates the
    ``wizard`` extra not being installed without actually uninstalling
    pywebview from the test environment.
    """
    real_import = builtins.__import__
    blocked_prefixes = ("regstack.wizard.oauth_google", "regstack.wizard.theme_designer")

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if any(name == p or name.startswith(p + ".") for p in blocked_prefixes):
            raise ImportError(f"simulated missing wizard extra (blocked {name})")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    yield


def test_oauth_setup_without_wizard_extra_prints_hint(
    block_wizard_imports: None,
) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["oauth", "setup"])
    assert result.exit_code == 2
    assert "wizard" in result.output + result.stderr
    assert "regstack[wizard]" in result.output + result.stderr
    # The hint mentions the specific subcommand the user typed.
    assert "regstack oauth setup" in result.output + result.stderr


def test_theme_design_without_wizard_extra_prints_hint(
    block_wizard_imports: None,
) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["theme", "design"])
    assert result.exit_code == 2
    assert "wizard" in result.output + result.stderr
    assert "regstack[wizard]" in result.output + result.stderr
    assert "regstack theme design" in result.output + result.stderr
