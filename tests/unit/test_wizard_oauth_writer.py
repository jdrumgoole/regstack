"""Tests for the OAuth-wizard's config + secrets-file merge.

Golden-file style: each test sets up a starting state on disk, runs
the merge, and asserts the resulting files have the right shape AND
that unrelated content is untouched.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import tomlkit

from regstack.wizard.oauth_google.writer import (
    CONFIG_FILE,
    SECRETS_ENV_KEY,
    SECRETS_FILE,
    compute_default_redirect_uri,
    detect_existing_oauth,
    merge_into_config,
)


def _good_call(target_dir: Path, **overrides) -> dict:
    base = dict(
        target_dir=target_dir,
        base_url="http://localhost:8000",
        api_prefix="/api/auth",
        client_id="12345-abc.apps.googleusercontent.com",
        client_secret="GOCSPX-secretvalue1234",
        auto_link_verified_emails=False,
        enforce_mfa_on_oauth_signin=False,
        custom_redirect_uri=None,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# compute_default_redirect_uri
# ---------------------------------------------------------------------------


def test_default_redirect_uri_basic() -> None:
    assert (
        compute_default_redirect_uri("http://localhost:8000")
        == "http://localhost:8000/api/auth/oauth/google/callback"
    )


def test_default_redirect_uri_strips_trailing_slashes() -> None:
    assert (
        compute_default_redirect_uri("https://app.example.com/", "/api/auth/")
        == "https://app.example.com/api/auth/oauth/google/callback"
    )


def test_default_redirect_uri_custom_prefix() -> None:
    assert (
        compute_default_redirect_uri("http://localhost:8000", "/internal")
        == "http://localhost:8000/internal/oauth/google/callback"
    )


# ---------------------------------------------------------------------------
# detect_existing_oauth
# ---------------------------------------------------------------------------


def test_detect_existing_no_file(tmp_path: Path) -> None:
    assert detect_existing_oauth(tmp_path / CONFIG_FILE) is False


def test_detect_existing_no_oauth_table(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text('app_name = "X"\nenable_oauth = false\n')
    assert detect_existing_oauth(tmp_path / CONFIG_FILE) is False


def test_detect_existing_with_oauth_table(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        'app_name = "X"\n[oauth]\ngoogle_client_id = "x.apps.googleusercontent.com"\n'
    )
    assert detect_existing_oauth(tmp_path / CONFIG_FILE) is True


def test_detect_existing_handles_corrupt_toml(tmp_path: Path) -> None:
    """A malformed file is treated as 'no existing OAuth'."""
    (tmp_path / CONFIG_FILE).write_text("this is = not [valid toml")
    assert detect_existing_oauth(tmp_path / CONFIG_FILE) is False


# ---------------------------------------------------------------------------
# merge_into_config — fresh write
# ---------------------------------------------------------------------------


def test_merge_creates_files_when_target_dir_empty(tmp_path: Path) -> None:
    result = merge_into_config(**_good_call(tmp_path))

    cfg = tomlkit.parse((tmp_path / CONFIG_FILE).read_text())
    assert cfg["enable_oauth"] is True
    assert cfg["oauth"]["google_client_id"] == "12345-abc.apps.googleusercontent.com"
    assert cfg["oauth"]["auto_link_verified_emails"] is False
    assert cfg["oauth"]["enforce_mfa_on_oauth_signin"] is False
    # No google_redirect_uri when default suffices.
    assert "google_redirect_uri" not in cfg["oauth"]

    secrets = (tmp_path / SECRETS_FILE).read_text()
    assert f"{SECRETS_ENV_KEY}=GOCSPX-secretvalue1234" in secrets

    assert result.replaced_existing is False
    assert result.config_diff == "added [oauth] table"
    assert result.secrets_diff == f"added {SECRETS_ENV_KEY}"


def test_secrets_file_chmod_0600(tmp_path: Path) -> None:
    merge_into_config(**_good_call(tmp_path))
    mode = stat.S_IMODE(os.stat(tmp_path / SECRETS_FILE).st_mode)
    assert mode == 0o600, oct(mode)


def test_custom_redirect_uri_written(tmp_path: Path) -> None:
    merge_into_config(
        **_good_call(
            tmp_path,
            custom_redirect_uri="https://proxy.example.com/auth/oauth/google/callback",
        )
    )
    cfg = tomlkit.parse((tmp_path / CONFIG_FILE).read_text())
    assert (
        cfg["oauth"]["google_redirect_uri"]
        == "https://proxy.example.com/auth/oauth/google/callback"
    )


# ---------------------------------------------------------------------------
# merge_into_config — preserve existing config
# ---------------------------------------------------------------------------


_EXISTING_TOML = """\
# Hand-written config — this comment must survive the merge.
app_name = "MyApp"
base_url = "https://app.example.com"

require_verification = true
allow_registration = true
enable_admin_router = true

[email]
backend = "smtp"
from_address = "noreply@example.com"
smtp_host = "smtp.example.com"
smtp_port = 587

[sms]
backend = "null"
"""


def test_merge_preserves_unrelated_top_level_keys(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(_EXISTING_TOML)
    merge_into_config(**_good_call(tmp_path))

    cfg = tomlkit.parse((tmp_path / CONFIG_FILE).read_text())
    assert cfg["app_name"] == "MyApp"
    assert cfg["base_url"] == "https://app.example.com"
    assert cfg["require_verification"] is True
    assert cfg["enable_admin_router"] is True


def test_merge_preserves_unrelated_tables(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(_EXISTING_TOML)
    merge_into_config(**_good_call(tmp_path))

    cfg = tomlkit.parse((tmp_path / CONFIG_FILE).read_text())
    assert cfg["email"]["backend"] == "smtp"
    assert cfg["email"]["smtp_host"] == "smtp.example.com"
    assert cfg["email"]["smtp_port"] == 587
    assert cfg["sms"]["backend"] == "null"


def test_merge_sets_enable_oauth_true(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(_EXISTING_TOML + "\nenable_oauth = false\n")
    merge_into_config(**_good_call(tmp_path))
    cfg = tomlkit.parse((tmp_path / CONFIG_FILE).read_text())
    assert cfg["enable_oauth"] is True


def test_merge_preserves_comments(tmp_path: Path) -> None:
    """tomlkit round-trips comments — the hand-written # comment in
    the test fixture must still be in the file after merge."""
    (tmp_path / CONFIG_FILE).write_text(_EXISTING_TOML)
    merge_into_config(**_good_call(tmp_path))
    text = (tmp_path / CONFIG_FILE).read_text()
    assert "# Hand-written config" in text


# ---------------------------------------------------------------------------
# merge_into_config — replacing existing oauth
# ---------------------------------------------------------------------------


def test_merge_replaces_existing_oauth_table(tmp_path: Path) -> None:
    starting = (
        _EXISTING_TOML
        + """
[oauth]
google_client_id = "old.apps.googleusercontent.com"
google_redirect_uri = "https://old.example.com/cb"
auto_link_verified_emails = true
state_ttl_seconds = 600
"""
    )
    (tmp_path / CONFIG_FILE).write_text(starting)
    result = merge_into_config(**_good_call(tmp_path))

    cfg = tomlkit.parse((tmp_path / CONFIG_FILE).read_text())
    assert cfg["oauth"]["google_client_id"] == "12345-abc.apps.googleusercontent.com"
    # auto_link reverts to the new (False) value.
    assert cfg["oauth"]["auto_link_verified_emails"] is False
    # Stale keys (google_redirect_uri override + state_ttl_seconds) are gone.
    assert "google_redirect_uri" not in cfg["oauth"]
    assert "state_ttl_seconds" not in cfg["oauth"]
    assert result.replaced_existing is True
    assert result.config_diff == "replaced [oauth] table"


# ---------------------------------------------------------------------------
# Secrets file
# ---------------------------------------------------------------------------


def test_secrets_appended_to_existing_file(tmp_path: Path) -> None:
    existing = "REGSTACK_JWT_SECRET=abc\nOTHER_VAR=xyz\n"
    (tmp_path / SECRETS_FILE).write_text(existing)
    merge_into_config(**_good_call(tmp_path))

    text = (tmp_path / SECRETS_FILE).read_text()
    # Existing lines preserved.
    assert "REGSTACK_JWT_SECRET=abc" in text
    assert "OTHER_VAR=xyz" in text
    assert f"{SECRETS_ENV_KEY}=GOCSPX-secretvalue1234" in text


def test_secrets_replaced_in_place_no_duplicate(tmp_path: Path) -> None:
    existing = f"{SECRETS_ENV_KEY}=old-value\nREGSTACK_JWT_SECRET=preserved\n"
    (tmp_path / SECRETS_FILE).write_text(existing)
    result = merge_into_config(**_good_call(tmp_path))

    text = (tmp_path / SECRETS_FILE).read_text()
    assert "REGSTACK_JWT_SECRET=preserved" in text
    assert "old-value" not in text
    # Exactly one occurrence of the env var.
    assert text.count(f"{SECRETS_ENV_KEY}=") == 1
    assert result.replaced_existing is True


def test_secrets_file_trailing_newline(tmp_path: Path) -> None:
    merge_into_config(**_good_call(tmp_path))
    text = (tmp_path / SECRETS_FILE).read_text()
    assert text.endswith("\n")


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_twice_leaves_one_oauth_table(tmp_path: Path) -> None:
    merge_into_config(**_good_call(tmp_path))
    merge_into_config(**_good_call(tmp_path))
    cfg = tomlkit.parse((tmp_path / CONFIG_FILE).read_text())
    # Only one [oauth] table; tomlkit would have raised if we'd
    # duplicated it.
    assert cfg["oauth"]["google_client_id"] == "12345-abc.apps.googleusercontent.com"


def test_running_twice_leaves_one_secret_line(tmp_path: Path) -> None:
    merge_into_config(**_good_call(tmp_path))
    merge_into_config(**_good_call(tmp_path))
    text = (tmp_path / SECRETS_FILE).read_text()
    assert text.count(f"{SECRETS_ENV_KEY}=") == 1


def test_target_dir_created_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "fresh" / "subdir"
    assert not nested.exists()
    merge_into_config(**_good_call(nested))
    assert (nested / CONFIG_FILE).exists()
    assert (nested / SECRETS_FILE).exists()
