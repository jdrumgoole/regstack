"""Non-clobbering merge of SES configuration into an existing config.

Same shape as :mod:`regstack.wizard.oauth_google.writer` but operates
on the ``[email]`` sub-table and writes to a different secrets-env key.

Design constraints (from the SES wizard scope):

- Set ``[email].backend = "ses"``.
- Set ``[email].ses_region`` to the chosen region.
- Write exactly ONE of: ``ses_profile``, ``ses_access_key_id``, or
  no credential key (the "chain" mode delegates to boto3's default
  credential resolution).
- ``ses_secret_access_key`` (when using explicit creds) goes to
  ``regstack.secrets.env``, never to the TOML.
- Preserve unrelated ``[email]`` sub-keys (e.g. an existing
  ``from_name`` line a host has hand-edited) by editing the table
  in place rather than replacing it wholesale.
- Secrets file gets ``chmod 0600``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit
from tomlkit import TOMLDocument

CONFIG_FILE = "regstack.toml"
SECRETS_FILE = "regstack.secrets.env"

SECRETS_ENV_KEY = "REGSTACK_EMAIL__SES_SECRET_ACCESS_KEY"

CredentialSource = Literal["profile", "explicit", "chain"]


@dataclass(slots=True)
class WriteResult:
    """Summary of a successful merge.

    ``config_diff`` and ``secrets_diff`` are short human-readable
    descriptions for the SPA to render; they are not unified diffs.
    """

    config_path: Path
    secrets_path: Path
    config_diff: str
    secrets_diff: str
    replaced_existing: bool


def merge_into_config(
    *,
    target_dir: Path,
    ses_region: str,
    from_address: str,
    credential_source: CredentialSource,
    ses_profile: str | None = None,
    ses_access_key_id: str | None = None,
    ses_secret_access_key: str | None = None,
    dry_run: bool = False,
) -> WriteResult:
    """Merge SES values into ``regstack.toml`` + ``regstack.secrets.env``.

    Reads the existing files (if present), updates them in place
    preserving non-SES content, and writes back. Idempotent: re-
    running with the same inputs touches mtimes but leaves content
    unchanged.

    Args:
        target_dir: Directory containing (or to receive) the config
            files. Created if missing.
        ses_region: AWS region (e.g. ``"eu-west-1"``). Validated by
            :func:`regstack.wizard.ses.validators._step_region` before
            we get here.
        from_address: Sender email. Lands in ``[email].from_address``.
        credential_source: One of ``"profile"``, ``"explicit"``,
            ``"chain"``. Determines which (if any) credential keys
            are written.
        ses_profile: AWS profile name. Required when
            ``credential_source == "profile"``.
        ses_access_key_id: AWS access key ID. Required when
            ``credential_source == "explicit"``. Lands in the TOML
            (not a secret).
        ses_secret_access_key: AWS secret access key. Required when
            ``credential_source == "explicit"``. Lands in
            ``regstack.secrets.env``, never in the TOML.

    Returns:
        :class:`WriteResult` describing what changed.
    """
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    config_path = (target_dir / CONFIG_FILE).resolve()
    secrets_path = (target_dir / SECRETS_FILE).resolve()

    config_doc, replaced_email_block = _update_config(
        config_path,
        ses_region=ses_region,
        from_address=from_address,
        credential_source=credential_source,
        ses_profile=ses_profile,
        ses_access_key_id=ses_access_key_id,
    )
    if not dry_run:
        config_path.write_text(tomlkit.dumps(config_doc), encoding="utf-8")

    replaced_secret = _update_secrets(
        secrets_path,
        secret=ses_secret_access_key if credential_source == "explicit" else None,
        dry_run=dry_run,
    )

    return WriteResult(
        config_path=config_path,
        secrets_path=secrets_path,
        config_diff=("replaced [email] table" if replaced_email_block else "added [email] table"),
        secrets_diff=(
            f"replaced {SECRETS_ENV_KEY}"
            if replaced_secret == "replaced"
            else (
                f"added {SECRETS_ENV_KEY}"
                if replaced_secret == "added"
                else (
                    f"removed {SECRETS_ENV_KEY}"
                    if replaced_secret == "removed"
                    else "no secrets change"
                )
            )
        ),
        replaced_existing=replaced_email_block or replaced_secret in {"replaced", "removed"},
    )


def detect_existing_ses(config_path: Path) -> bool:
    """Whether ``regstack.toml`` already has ``[email].backend = "ses"``.

    Used by the wizard's "detect existing config" step (1) to decide
    whether to require an explicit replace-confirmation gate. Any
    other ``[email].backend`` value (or no ``[email]`` table at all)
    returns False — the wizard proceeds without the gate.
    """
    if not config_path.exists():
        return False
    try:
        doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    email = doc.get("email")
    if not isinstance(email, dict):
        return False
    return email.get("backend") == "ses"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _update_config(
    config_path: Path,
    *,
    ses_region: str,
    from_address: str,
    credential_source: CredentialSource,
    ses_profile: str | None,
    ses_access_key_id: str | None,
) -> tuple[TOMLDocument, bool]:
    if config_path.exists():
        doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()

    replaced = "email" in doc and isinstance(doc["email"], dict)
    email_table = doc.get("email")
    if not isinstance(email_table, dict):
        email_table = tomlkit.table()
        doc["email"] = email_table

    # The SES-managed fields. Anything else under [email] (e.g. a
    # hand-edited from_name, log_bodies override) is left untouched.
    email_table["backend"] = "ses"
    email_table["from_address"] = from_address
    email_table["ses_region"] = ses_region

    # Wipe any conflicting credential keys from a prior wizard run
    # before writing the new mode's keys. Stops a profile-mode run
    # from coexisting with a stale ses_access_key_id line.
    for stale in ("ses_profile", "ses_access_key_id"):
        if stale in email_table:
            del email_table[stale]

    if credential_source == "profile":
        assert ses_profile is not None, "ses_profile required for profile mode"
        email_table["ses_profile"] = ses_profile
    elif credential_source == "explicit":
        assert ses_access_key_id is not None, "ses_access_key_id required for explicit mode"
        email_table["ses_access_key_id"] = ses_access_key_id
    # "chain" writes neither — boto3's default credential chain runs.

    return doc, replaced


def _update_secrets(
    secrets_path: Path, *, secret: str | None, dry_run: bool = False
) -> Literal["added", "replaced", "removed", "noop"]:
    """Write/replace the secret-access-key line.

    When ``secret`` is None (profile or chain mode), the function
    *removes* any pre-existing line — switching mode in a re-run
    shouldn't leave a stale secret on disk. When ``secret`` is
    provided, the line is added or replaced as appropriate.

    With ``dry_run=True`` the function still reports what would have
    changed but does not write.
    """
    lines = secrets_path.read_text(encoding="utf-8").splitlines() if secrets_path.exists() else []

    new_lines: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{SECRETS_ENV_KEY}="):
            found = True
            if secret is not None:
                new_lines.append(f"{SECRETS_ENV_KEY}={secret}")
            # else: drop the line entirely
        else:
            new_lines.append(line)

    if secret is None and not found:
        return "noop"
    if secret is None and found:
        if not dry_run:
            _write_secrets(secrets_path, new_lines)
        return "removed"
    if secret is not None and not found:
        new_lines.append(f"{SECRETS_ENV_KEY}={secret}")
        if not dry_run:
            _write_secrets(secrets_path, new_lines)
        return "added"
    # secret is not None and found: replaced in-place above.
    if not dry_run:
        _write_secrets(secrets_path, new_lines)
    return "replaced"


def _write_secrets(secrets_path: Path, lines: list[str]) -> None:
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    secrets_path.write_text(text, encoding="utf-8")
    os.chmod(secrets_path, 0o600)


# Used by callers (the routes layer + the CLI) to fully describe the
# intended change without writing it.
def _result_as_dict(result: WriteResult) -> dict[str, Any]:
    return {
        "config_path": str(result.config_path),
        "secrets_path": str(result.secrets_path),
        "config_diff": result.config_diff,
        "secrets_diff": result.secrets_diff,
        "replaced_existing": result.replaced_existing,
    }


__all__ = [
    "CONFIG_FILE",
    "SECRETS_ENV_KEY",
    "SECRETS_FILE",
    "CredentialSource",
    "WriteResult",
    "detect_existing_ses",
    "merge_into_config",
]
