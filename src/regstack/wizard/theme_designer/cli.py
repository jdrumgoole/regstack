"""``regstack theme design`` Click subcommand.

Two modes:

- **Interactive (default).** Opens the designer in a pywebview window
  pointed at a local 127.0.0.1 server. Closing the window tears the
  server down.
- **``--print-only``.** Skips the GUI, takes ``--var name=value``
  pairs (repeatable), validates them, writes
  ``regstack-theme.css``, and prints a JSON summary. Useful for
  scripted theming and CI.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from regstack.cli._paths import resolve_target_dir
from regstack.wizard.theme_designer.routes import DEFAULT_LIGHT
from regstack.wizard.theme_designer.server import make_designer_server, serve
from regstack.wizard.theme_designer.validators import validate_vars
from regstack.wizard.theme_designer.writer import THEME_FILE, save_theme


@click.command(
    name="design",
    help=(
        "Open a live designer for regstack-theme.css. Live preview of the "
        "bundled SSR widgets, controls for every --rs-* variable, "
        "non-clobbering save."
    ),
)
@click.option(
    "--config",
    "config_path_in",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Path to regstack.toml or the directory to write "
        "regstack-theme.css into (default: current directory)."
    ),
)
@click.option(
    "--target",
    "target_path_in",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="DEPRECATED: use --config.",
)
@click.option(
    "--filename",
    default=THEME_FILE,
    show_default=True,
    help="Output filename.",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="Pin the designer's TCP port (default: random free port).",
)
@click.option(
    "--headless",
    is_flag=True,
    help=(
        "Don't open a GUI. Validate from --var pairs, write "
        "regstack-theme.css, print a JSON summary. For CI and "
        "headless hosts."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Implies --headless. Validate but do not write the file.",
)
@click.option(
    "--print-only",
    is_flag=True,
    help="DEPRECATED: alias for --headless.",
)
@click.option(
    "--var",
    "var_pairs",
    multiple=True,
    help=(
        "Used with --headless / --dry-run. Repeatable. Accepts NAME=VALUE pairs, e.g. "
        "--var --rs-accent=#0d9488. Prefix with `dark:` to set in the dark "
        "scope, e.g. --var dark:--rs-accent=#2dd4bf."
    ),
)
def design(
    config_path_in: Path | None,
    target_path_in: Path | None,
    filename: str,
    port: int | None,
    headless: bool,
    dry_run: bool,
    print_only: bool,
    var_pairs: tuple[str, ...],
) -> None:
    target_dir = resolve_target_dir(config=config_path_in, target=target_path_in)
    if print_only:
        click.echo(
            click.style(
                "Deprecation: --print-only is deprecated; use --headless. "
                "--print-only will be removed in 1.0.",
                fg="yellow",
            ),
            err=True,
        )
        headless = True
    if dry_run:
        headless = True
    if headless:
        _run_headless(
            target_dir=target_dir,
            filename=filename,
            var_pairs=var_pairs,
            dry_run=dry_run,
        )
        return
    _run_gui(target_dir=target_dir, filename=filename, port=port)


def _run_headless(
    *,
    target_dir: Path,
    filename: str,
    var_pairs: tuple[str, ...],
    dry_run: bool,
) -> None:
    light: dict[str, str] = {}
    dark: dict[str, str] = {}

    for raw in var_pairs:
        scope, name, value = _parse_var_pair(raw)
        if scope == "dark":
            dark[name] = value
        else:
            light[name] = value

    if not light and not dark:
        click.echo(
            "No --var pairs supplied; writing all defaults. Pass --var to override.",
            err=True,
        )
        light = dict(DEFAULT_LIGHT)

    light_result = validate_vars(light, scope="light")
    dark_result = validate_vars(dark, scope="dark")
    if not light_result.ok or not dark_result.ok:
        click.echo("Validation failed:", err=True)
        for err in (*light_result.errors, *dark_result.errors):
            click.echo(f"  - {err.field}: {err.message}", err=True)
        sys.exit(2)

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    result = save_theme(target_dir, light=light, dark=dark, filename=filename, dry_run=dry_run)
    click.echo(
        json.dumps(
            {
                "target_path": str(result.target_path),
                "light_count": result.light_count,
                "dark_count": result.dark_count,
                "bytes_written": result.bytes_written,
                "dry_run": dry_run,
            },
            indent=2,
        )
    )


def _parse_var_pair(raw: str) -> tuple[str, str, str]:
    """Split ``[dark:]NAME=VALUE`` into ``(scope, name, value)``."""
    scope = "light"
    payload = raw
    if payload.startswith("dark:"):
        scope = "dark"
        payload = payload[len("dark:") :]
    elif payload.startswith("light:"):
        payload = payload[len("light:") :]
    if "=" not in payload:
        raise click.BadParameter(f"--var must be NAME=VALUE (got {raw!r})", param_hint="--var")
    name, value = payload.split("=", 1)
    return scope, name.strip(), value.strip()


def _run_gui(*, target_dir: Path, filename: str, port: int | None) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    server = make_designer_server(target_dir=target_dir, port=port, filename=filename)
    click.echo(f"Designer URL: {server.url}")
    click.echo("Opening designer window… close it to exit.")

    from regstack.wizard.theme_designer.window import (
        DesignerWindowError,
        open_designer_window,
    )

    server_thread_done: asyncio.Event = asyncio.Event()

    def _serve_forever() -> None:
        async def _go() -> None:
            try:
                await serve(server)
            finally:
                server_thread_done.set()

        asyncio.run(_go())

    import threading

    thread = threading.Thread(target=_serve_forever, daemon=True)
    thread.start()

    try:
        open_designer_window(server)
    except DesignerWindowError as exc:
        click.echo(f"Error: {exc}", err=True)
        server.settings.shutdown_event.set()
        thread.join(timeout=5)
        sys.exit(1)
    finally:
        server.settings.shutdown_event.set()
        thread.join(timeout=5)


__all__ = ["design"]
