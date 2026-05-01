"""Smoke tests for ``regstack oauth setup --print-only``.

The GUI path can't be exercised under pytest (it would block on
``webview.start``). The ``--print-only`` mode runs the same
validation + merge logic and writes the same files, so this is the
cheapest end-to-end check we have outside the Playwright suite.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from click.testing import CliRunner

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
