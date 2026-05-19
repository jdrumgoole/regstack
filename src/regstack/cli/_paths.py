"""Shared CLI helpers for resolving the regstack config target.

As of 0.8.x the canonical CLI flag is ``--config``. It accepts either:

* a path to a regstack TOML file (``./regstack.toml``), or
* a path to a directory containing or to-receive that file (``./conf/``).

A legacy ``--target`` flag is still accepted on commands that write
config (``init``, ``oauth setup``, ``ses setup``, ``theme design``);
using it emits a deprecation warning.

This module is the single source of truth for that resolution so each
command stays a thin Click wrapper.
"""

from __future__ import annotations

from pathlib import Path

import click

CONFIG_FILE = "regstack.toml"

_DEPRECATION_HINT = (
    "Deprecation: --target is deprecated; use --config (accepts a file or a "
    "directory). --target will be removed in 1.0."
)


def resolve_target_dir(
    *,
    config: Path | None,
    target: Path | None,
) -> Path:
    """Resolve the directory the command should write into.

    ``config`` is the canonical flag. ``target`` is the legacy alias;
    if it's the one supplied, a deprecation warning is emitted to
    stderr (once per invocation) before the value is honoured.

    Raises ``click.UsageError`` if both are supplied.
    """
    if config is not None and target is not None:
        raise click.UsageError(
            "--config and --target are mutually exclusive. Use --config "
            "(--target is the deprecated alias)."
        )

    if target is not None:
        click.echo(click.style(_DEPRECATION_HINT, fg="yellow"), err=True)
        value: Path | None = target
    else:
        value = config

    if value is None:
        return Path.cwd().resolve()

    p = value.resolve()
    # File or dir? If it's a file (or looks like one — name ends with .toml),
    # operate in its parent directory.
    if p.is_file() or p.suffix == ".toml":
        return p.parent
    return p


def resolve_toml_path(config: Path | None) -> Path | None:
    """Resolve ``--config`` for read-only commands (doctor, migrate, create-admin).

    Returns the path to the TOML file, or ``None`` if the flag was
    omitted (caller falls back to env/cwd discovery).

    If a directory is supplied, looks for ``regstack.toml`` inside it.
    The returned path is not required to exist — load_runtime_config
    surfaces the error with a clearer message.
    """
    if config is None:
        return None
    p = config.resolve()
    if p.is_dir():
        return p / CONFIG_FILE
    return p


__all__ = ["CONFIG_FILE", "resolve_target_dir", "resolve_toml_path"]
