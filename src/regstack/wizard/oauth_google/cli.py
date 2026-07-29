"""``regstack oauth setup`` Click subcommand.

Two modes:

- **Interactive (default).** Spawns the local wizard server on
  ``127.0.0.1:<random>`` and opens a native pywebview window
  pointed at it. The window is the only client; closing it tears
  the server down.
- **``--print-only``.** Skips the GUI entirely. Reads
  ``--client-id`` / ``--client-secret`` / ``--base-url`` from the
  CLI flags, runs the same validation + merge that the wizard
  would, and prints the resulting diff. Useful for CI sanity
  checks and for headless hosts.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from regstack.cli._paths import resolve_target_dir
from regstack.wizard.oauth_google.server import make_wizard_server, serve
from regstack.wizard.oauth_google.validators import validate_all
from regstack.wizard.oauth_google.writer import merge_into_config


@click.group(help="OAuth provider setup wizards.")
def oauth() -> None:
    pass


@oauth.command(
    help=(
        "Open a guided wizard window to register a Google OAuth client "
        "and merge the credentials into regstack.toml + regstack.secrets.env."
    )
)
@click.option(
    "--config",
    "config_path_in",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to regstack.toml or its directory (default: current directory).",
)
@click.option(
    "--target",
    "target_path_in",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="DEPRECATED: use --config.",
)
@click.option(
    "--api-prefix",
    default="/api/auth",
    show_default=True,
    help="Router prefix the host mounts regstack under.",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="Pin the wizard server's TCP port (default: random free port).",
)
@click.option(
    "--headless",
    is_flag=True,
    help=(
        "Don't open a GUI. Validate from --client-id / --client-secret / "
        "--base-url, write the config, print the diff. For CI and "
        "headless hosts."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help=(
        "Implies --headless. Validate and print the diff that WOULD be "
        "written, but do not touch the files. Exits 0 on success, "
        "2 on validation failure."
    ),
)
@click.option(
    "--print-only",
    is_flag=True,
    help="DEPRECATED: alias for --headless.",
)
@click.option("--client-id", default=None, help="Used only with --headless / --dry-run.")
@click.option("--client-secret", default=None, help="Used only with --headless / --dry-run.")
@click.option(
    "--base-url",
    default="http://localhost:8000",
    show_default=True,
    help="Used only with --headless / --dry-run.",
)
@click.option(
    "--auto-link/--no-auto-link",
    "auto_link_verified_emails",
    default=False,
    help="Used only with --headless / --dry-run.",
)
@click.option(
    "--mfa/--no-mfa",
    "enforce_mfa_on_oauth_signin",
    default=False,
    help="Used only with --headless / --dry-run.",
)
def setup(
    config_path_in: Path | None,
    target_path_in: Path | None,
    api_prefix: str,
    port: int | None,
    headless: bool,
    dry_run: bool,
    print_only: bool,
    client_id: str | None,
    client_secret: str | None,
    base_url: str,
    auto_link_verified_emails: bool,
    enforce_mfa_on_oauth_signin: bool,
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
            api_prefix=api_prefix,
            base_url=base_url,
            client_id=client_id or "",
            client_secret=client_secret or "",
            auto_link_verified_emails=auto_link_verified_emails,
            enforce_mfa_on_oauth_signin=enforce_mfa_on_oauth_signin,
            dry_run=dry_run,
        )
        return

    _run_gui(target_dir=target_dir, api_prefix=api_prefix, port=port)


def _run_headless(
    *,
    target_dir: Path,
    api_prefix: str,
    base_url: str,
    client_id: str,
    client_secret: str,
    auto_link_verified_emails: bool,
    enforce_mfa_on_oauth_signin: bool,
    dry_run: bool,
) -> None:
    inputs = {
        "existing_oauth": False,
        "replace_existing": True,
        "base_url": base_url,
        "client_id": client_id,
        "client_secret": client_secret,
        "auto_link_verified_emails": auto_link_verified_emails,
        "enforce_mfa_on_oauth_signin": enforce_mfa_on_oauth_signin,
    }
    result = validate_all(inputs)
    if not result.ok:
        click.echo("Validation failed:", err=True)
        for err in result.errors:
            click.echo(f"  - {err.field}: {err.message}", err=True)
        sys.exit(2)

    target_dir.mkdir(parents=True, exist_ok=True)
    write_result = merge_into_config(
        target_dir=target_dir,
        base_url=base_url,
        api_prefix=api_prefix,
        client_id=client_id,
        client_secret=client_secret,
        auto_link_verified_emails=auto_link_verified_emails,
        enforce_mfa_on_oauth_signin=enforce_mfa_on_oauth_signin,
        dry_run=dry_run,
    )
    click.echo(
        json.dumps(
            {
                "config_path": str(write_result.config_path),
                "config_diff": write_result.config_diff,
                "secrets_path": str(write_result.secrets_path),
                "secrets_diff": write_result.secrets_diff,
                "replaced_existing": write_result.replaced_existing,
                "dry_run": dry_run,
            },
            indent=2,
        )
    )


def _run_gui(*, target_dir: Path, api_prefix: str, port: int | None) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / "regstack.toml"
    existing_base_url = _existing_base_url(config_path)
    server = make_wizard_server(
        target_dir=target_dir,
        api_prefix=api_prefix,
        existing_base_url=existing_base_url,
        port=port,
    )
    click.echo(f"Wizard URL: {server.url}")
    click.echo("Opening wizard window… close it to exit.")

    # Lazy-import: a print-only run on a headless host shouldn't hit
    # the pywebview import path at all.
    from regstack.wizard.oauth_google.window import (
        WizardWindowError,
        open_wizard_window,
    )

    def _serve_forever() -> None:
        # The loop lives entirely inside this thread. Nothing outside it may
        # touch an asyncio primitive belonging to it — shutdown crosses the
        # thread boundary via the threading.Event in server.settings.
        asyncio.run(serve(server))

    import threading

    thread = threading.Thread(target=_serve_forever, daemon=True)
    thread.start()

    try:
        open_wizard_window(server)
    except WizardWindowError as exc:
        click.echo(f"Error: {exc}", err=True)
        server.settings.shutdown_event.set()
        thread.join(timeout=5)
        sys.exit(1)
    finally:
        server.settings.shutdown_event.set()
        thread.join(timeout=5)


def _existing_base_url(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    try:
        import tomllib

        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = data.get("base_url")
    return value if isinstance(value, str) and value else None


__all__ = ["oauth", "setup"]
