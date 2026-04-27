from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from regstack.config.schema import RegStackConfig

_DEFAULT_TOML_NAMES = ("regstack.toml",)
_DEFAULT_SECRETS_NAMES = ("regstack.secrets.env",)


def _find_first(names: tuple[str, ...]) -> Path | None:
    cwd = Path.cwd()
    for name in names:
        candidate = cwd / name
        if candidate.is_file():
            return candidate
    return None


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser — `KEY=value` per line, no shell features."""
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def _flatten_for_env(d: Mapping[str, Any], prefix: str = "REGSTACK_") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in d.items():
        full_key = f"{prefix}{key.upper()}"
        if isinstance(value, Mapping):
            out.update(_flatten_for_env(value, prefix=f"{full_key}__"))
        elif isinstance(value, list):
            out[full_key] = ",".join(str(item) for item in value)
        elif isinstance(value, bool):
            out[full_key] = "true" if value else "false"
        elif value is None:
            continue
        else:
            out[full_key] = str(value)
    return out


def load_config(
    toml_path: Path | str | None = None,
    secrets_env_path: Path | str | None = None,
    **overrides: object,
) -> RegStackConfig:
    """Build a ``RegStackConfig`` by merging defaults, TOML, env, and kwargs.

    Highest priority wins:
        kwargs > os.environ > secrets.env > TOML > defaults.
    """
    env_overlay: dict[str, str] = {}

    toml_candidate: Path | None
    if toml_path is not None:
        toml_candidate = Path(toml_path)
    elif (env_path := os.environ.get("REGSTACK_CONFIG")) is not None:
        toml_candidate = Path(env_path)
    else:
        toml_candidate = _find_first(_DEFAULT_TOML_NAMES)
    if toml_candidate is not None and toml_candidate.is_file():
        env_overlay.update(_flatten_for_env(_read_toml(toml_candidate)))

    secrets_candidate: Path | None
    if secrets_env_path is not None:
        secrets_candidate = Path(secrets_env_path)
    else:
        secrets_candidate = _find_first(_DEFAULT_SECRETS_NAMES)
    if secrets_candidate is not None and secrets_candidate.is_file():
        env_overlay.update(_parse_dotenv(secrets_candidate))

    # Real environment wins over TOML and secrets file.
    for key, value in os.environ.items():
        if key.startswith("REGSTACK_"):
            env_overlay[key] = value

    # pydantic-settings reads from os.environ; we apply our merged overlay
    # by patching it for the duration of construction.
    saved: dict[str, str | None] = {}
    try:
        for key, value in env_overlay.items():
            saved[key] = os.environ.get(key)
            os.environ[key] = value
        return RegStackConfig(**overrides)  # type: ignore[arg-type]
    finally:
        for key, prev in saved.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
