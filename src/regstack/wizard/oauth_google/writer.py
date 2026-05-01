"""Non-clobbering merge of OAuth credentials into an existing config.

The wizard's final step calls :func:`merge_into_config` with the
collected inputs. We round-trip ``regstack.toml`` through ``tomlkit``
so unrelated config (``app_name``, ``[email]``, feature flags, etc.)
keeps its formatting and comments. ``regstack.secrets.env`` is a
plain dotenv file edited line-by-line.

Design constraints — see the M4 wizard plan:

- Set top-level ``enable_oauth = true``.
- Add or replace the ``[oauth]`` table with the user-supplied values.
  Defaults (``state_ttl_seconds`` etc.) are NOT written so the file
  stays small.
- Append or replace ``REGSTACK_OAUTH__GOOGLE_CLIENT_SECRET`` in
  ``regstack.secrets.env`` (don't duplicate).
- Secrets file gets ``chmod 0600``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomlkit
from tomlkit import TOMLDocument

# Match the existing init wizard's filenames so a wizard-configured
# project drops in next to a wizard-bootstrapped one.
CONFIG_FILE = "regstack.toml"
SECRETS_FILE = "regstack.secrets.env"

# Env var name pydantic-settings expects for OAuthConfig.google_client_secret.
SECRETS_ENV_KEY = "REGSTACK_OAUTH__GOOGLE_CLIENT_SECRET"


@dataclass(slots=True)
class WriteResult:
    """Summary of a successful merge.

    Attributes:
        config_path: Absolute path of the written ``regstack.toml``.
        secrets_path: Absolute path of the written
            ``regstack.secrets.env``.
        config_diff: Human-readable description of what changed in
            the TOML file (e.g. ``"added [oauth] table"``).
        secrets_diff: Same for the secrets file.
        replaced_existing: True when the merge overwrote a previous
            ``[oauth]`` table or a previous client-secret line.
    """

    config_path: Path
    secrets_path: Path
    config_diff: str
    secrets_diff: str
    replaced_existing: bool


def compute_default_redirect_uri(base_url: str, api_prefix: str = "/api/auth") -> str:
    """Build the redirect URI Google must be told about.

    Mirrors :func:`regstack.routers.oauth._callback_url` so the wizard
    and the running app agree.

    Args:
        base_url: The host's public base URL, e.g.
            ``"http://localhost:8000"``.
        api_prefix: The router prefix the host mounts regstack under.
            Defaults to ``/api/auth`` to match
            :class:`~regstack.config.schema.RegStackConfig`.

    Returns:
        The full URI the host should add to Google's "Authorized
        redirect URIs" list, e.g.
        ``"http://localhost:8000/api/auth/oauth/google/callback"``.
    """
    return f"{base_url.rstrip('/')}{api_prefix.rstrip('/')}/oauth/google/callback"


def merge_into_config(
    *,
    target_dir: Path,
    base_url: str,
    api_prefix: str,
    client_id: str,
    client_secret: str,
    auto_link_verified_emails: bool,
    enforce_mfa_on_oauth_signin: bool,
    custom_redirect_uri: str | None = None,
) -> WriteResult:
    """Merge OAuth values into ``regstack.toml`` + ``regstack.secrets.env``.

    Reads existing files (if present), updates them in place
    preserving non-OAuth content, and writes back. Idempotent: re-
    running with the same inputs is a no-op aside from touching
    mtimes.

    Args:
        target_dir: Directory containing (or to receive) the config
            files. Created if missing.
        base_url: Host public URL, used to compute the default
            redirect URI when ``custom_redirect_uri`` is None.
        api_prefix: Router prefix the host mounts regstack under
            (typically ``/api/auth``). Used to compute the default
            redirect URI.
        client_id: Google OAuth 2.0 client ID.
        client_secret: Google OAuth 2.0 client secret. Goes into the
            secrets file, not the TOML.
        auto_link_verified_emails: See
            :class:`~regstack.config.schema.OAuthConfig`.
        enforce_mfa_on_oauth_signin: Same.
        custom_redirect_uri: Override the computed redirect URI when
            the host's public URL doesn't equal ``base_url`` (e.g.
            running behind a proxy that rewrites paths). When
            ``None``, omitted from the TOML so the running app uses
            its computed default.

    Returns:
        :class:`WriteResult` describing what changed.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = (target_dir / CONFIG_FILE).resolve()
    secrets_path = (target_dir / SECRETS_FILE).resolve()

    config_doc, replaced_oauth_block = _update_config(
        config_path,
        client_id=client_id,
        auto_link_verified_emails=auto_link_verified_emails,
        enforce_mfa_on_oauth_signin=enforce_mfa_on_oauth_signin,
        custom_redirect_uri=custom_redirect_uri,
    )
    config_path.write_text(tomlkit.dumps(config_doc), encoding="utf-8")

    replaced_secret = _update_secrets(secrets_path, client_secret)

    return WriteResult(
        config_path=config_path,
        secrets_path=secrets_path,
        config_diff=("replaced [oauth] table" if replaced_oauth_block else "added [oauth] table"),
        secrets_diff=(
            f"replaced {SECRETS_ENV_KEY}" if replaced_secret else f"added {SECRETS_ENV_KEY}"
        ),
        replaced_existing=replaced_oauth_block or replaced_secret,
    )


def detect_existing_oauth(config_path: Path) -> bool:
    """Whether ``regstack.toml`` already has an ``[oauth]`` table.

    Used by the wizard's "detect existing config" step (1) to decide
    whether to require an explicit replace-confirmation gate.
    """
    if not config_path.exists():
        return False
    try:
        doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return "oauth" in doc


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _update_config(
    config_path: Path,
    *,
    client_id: str,
    auto_link_verified_emails: bool,
    enforce_mfa_on_oauth_signin: bool,
    custom_redirect_uri: str | None,
) -> tuple[TOMLDocument, bool]:
    if config_path.exists():
        doc = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.document()

    # Preserve non-OAuth content exactly.
    doc["enable_oauth"] = True

    replaced = "oauth" in doc
    oauth = tomlkit.table()
    oauth.add("google_client_id", client_id)
    if custom_redirect_uri:
        oauth.add("google_redirect_uri", custom_redirect_uri)
    oauth.add("auto_link_verified_emails", auto_link_verified_emails)
    oauth.add("enforce_mfa_on_oauth_signin", enforce_mfa_on_oauth_signin)
    doc["oauth"] = oauth
    return doc, replaced


def _update_secrets(secrets_path: Path, client_secret: str) -> bool:
    """Write/replace the client-secret line. Returns True if a previous
    line was overwritten."""
    lines = secrets_path.read_text(encoding="utf-8").splitlines() if secrets_path.exists() else []

    replaced = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(f"{SECRETS_ENV_KEY}="):
            new_lines.append(f"{SECRETS_ENV_KEY}={client_secret}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{SECRETS_ENV_KEY}={client_secret}")

    text = "\n".join(new_lines)
    if not text.endswith("\n"):
        text += "\n"
    secrets_path.write_text(text, encoding="utf-8")
    os.chmod(secrets_path, 0o600)
    return replaced


def _validate_url_for_safety(url: str) -> bool:
    """Internal sanity check used in tests and route validation."""
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


__all__ = [
    "CONFIG_FILE",
    "SECRETS_ENV_KEY",
    "SECRETS_FILE",
    "WriteResult",
    "compute_default_redirect_uri",
    "detect_existing_oauth",
    "merge_into_config",
]


# Used by callers (the routes layer + the CLI) to fully describe the
# intended change without writing it. ``_dataclass_to_dict`` keeps the
# Any imports happy in mypy --strict environments.
def _result_as_dict(result: WriteResult) -> dict[str, Any]:
    return {
        "config_path": str(result.config_path),
        "secrets_path": str(result.secrets_path),
        "config_diff": result.config_diff,
        "secrets_diff": result.secrets_diff,
        "replaced_existing": result.replaced_existing,
    }
