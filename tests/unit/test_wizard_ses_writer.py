"""Golden-file tests for the SES wizard's config merger."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

from regstack.wizard.ses.writer import (
    CONFIG_FILE,
    SECRETS_ENV_KEY,
    SECRETS_FILE,
    detect_existing_ses,
    merge_into_config,
)

_PRE_EXISTING_TOML = """\
# regstack.toml — already configured with some content
app_name = "my-app"
base_url = "http://localhost:8000"

[oauth]
google_client_id = "12345.apps.googleusercontent.com"

[email]
backend = "console"
from_name = "MyApp customer service"  # operator-hand-edited; must survive the merge
"""


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _read_email_table(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["email"]


def test_merge_profile_mode(tmp_path: Path) -> None:
    cfg = tmp_path / CONFIG_FILE
    _write(cfg, _PRE_EXISTING_TOML)

    result = merge_into_config(
        target_dir=tmp_path,
        ses_region="eu-west-1",
        from_address="noreply@example.com",
        credential_source="profile",
        ses_profile="production",
    )
    assert result.replaced_existing  # there was an existing [email] table

    email = _read_email_table(cfg)
    assert email["backend"] == "ses"
    assert email["ses_region"] == "eu-west-1"
    assert email["from_address"] == "noreply@example.com"
    assert email["ses_profile"] == "production"
    # Unrelated [email] sub-key was preserved.
    assert email["from_name"] == "MyApp customer service"
    # No explicit-creds keys leaked.
    assert "ses_access_key_id" not in email
    assert "ses_secret_access_key" not in email
    # Unrelated top-level tables / fields preserved.
    full = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert full["app_name"] == "my-app"
    assert full["oauth"]["google_client_id"] == "12345.apps.googleusercontent.com"
    # No secret was written (profile mode).
    secrets = tmp_path / SECRETS_FILE
    assert (not secrets.exists()) or SECRETS_ENV_KEY not in secrets.read_text()


def test_merge_explicit_mode_writes_secret_to_env_file(tmp_path: Path) -> None:
    result = merge_into_config(
        target_dir=tmp_path,
        ses_region="us-east-1",
        from_address="noreply@example.com",
        credential_source="explicit",
        ses_access_key_id="AKIAEXAMPLE",
        ses_secret_access_key="s3cret",
    )
    assert result.replaced_existing is False  # fresh files
    email = _read_email_table(tmp_path / CONFIG_FILE)
    assert email["ses_access_key_id"] == "AKIAEXAMPLE"
    # Secret never lands in TOML.
    assert "ses_secret_access_key" not in email
    secrets_path = tmp_path / SECRETS_FILE
    body = secrets_path.read_text(encoding="utf-8")
    assert f"{SECRETS_ENV_KEY}=s3cret" in body
    # 0600 perms.
    mode = stat.S_IMODE(os.stat(secrets_path).st_mode)
    assert mode == 0o600


def test_merge_chain_mode_writes_no_credential_keys(tmp_path: Path) -> None:
    merge_into_config(
        target_dir=tmp_path,
        ses_region="us-west-2",
        from_address="noreply@example.com",
        credential_source="chain",
    )
    email = _read_email_table(tmp_path / CONFIG_FILE)
    assert email["backend"] == "ses"
    assert email["ses_region"] == "us-west-2"
    for k in ("ses_profile", "ses_access_key_id", "ses_secret_access_key"):
        assert k not in email
    # Secrets file should either not exist or not contain our key.
    secrets = tmp_path / SECRETS_FILE
    assert (not secrets.exists()) or SECRETS_ENV_KEY not in secrets.read_text()


def test_switching_mode_removes_stale_keys(tmp_path: Path) -> None:
    """An operator who re-runs the wizard in profile mode after
    initially using explicit-creds should NOT end up with both
    ses_profile and ses_access_key_id sitting in the same TOML."""
    merge_into_config(
        target_dir=tmp_path,
        ses_region="us-east-1",
        from_address="noreply@example.com",
        credential_source="explicit",
        ses_access_key_id="AKIA-old",
        ses_secret_access_key="old-secret",
    )
    # Re-run in profile mode.
    merge_into_config(
        target_dir=tmp_path,
        ses_region="us-east-1",
        from_address="noreply@example.com",
        credential_source="profile",
        ses_profile="prod",
    )
    email = _read_email_table(tmp_path / CONFIG_FILE)
    assert email["ses_profile"] == "prod"
    assert "ses_access_key_id" not in email
    # And the secret should have been removed from the env file.
    body = (tmp_path / SECRETS_FILE).read_text(encoding="utf-8")
    assert SECRETS_ENV_KEY not in body


def test_idempotent_rerun(tmp_path: Path) -> None:
    args = dict(
        target_dir=tmp_path,
        ses_region="eu-west-1",
        from_address="noreply@example.com",
        credential_source="profile",
        ses_profile="prod",
    )
    merge_into_config(**args)  # type: ignore[arg-type]
    snapshot = (tmp_path / CONFIG_FILE).read_text(encoding="utf-8")
    merge_into_config(**args)  # type: ignore[arg-type]
    assert (tmp_path / CONFIG_FILE).read_text(encoding="utf-8") == snapshot


def test_detect_existing_ses_true_only_when_backend_is_ses(tmp_path: Path) -> None:
    cfg = tmp_path / CONFIG_FILE
    # No file yet.
    assert detect_existing_ses(cfg) is False
    # Different backend.
    _write(cfg, '[email]\nbackend = "console"\n')
    assert detect_existing_ses(cfg) is False
    # SES backend.
    _write(cfg, '[email]\nbackend = "ses"\n')
    assert detect_existing_ses(cfg) is True
    # Malformed TOML.
    _write(cfg, "this is not toml = =\n")
    assert detect_existing_ses(cfg) is False
