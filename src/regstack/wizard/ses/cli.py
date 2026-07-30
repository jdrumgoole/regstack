"""``regstack ses setup`` Click subcommand.

Two modes:

- **Interactive (default).** Spawns the local wizard server on
  ``127.0.0.1:<random>`` and opens a native pywebview window
  pointed at it.
- **``--print-only``.** Headless mode. Reads the necessary fields
  from CLI flags, runs the same validation + merge that the GUI
  would, and prints the resulting diff. AWS state checks are
  skipped — the CLI flags assume the operator already knows the
  credentials, region, and verified-domain story.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from regstack.cli._paths import resolve_target_dir
from regstack.wizard._scaffold import run_windowed
from regstack.wizard.ses.server import make_wizard_server
from regstack.wizard.ses.validators import (
    CREDENTIAL_SOURCES,
    KNOWN_SES_REGIONS,
    validate_all,
)
from regstack.wizard.ses.writer import merge_into_config


@click.group(help="SES email backend setup wizard.")
def ses() -> None:
    pass


@ses.command(
    help=(
        "Open a guided wizard window that validates SES against AWS and "
        "merges the result into regstack.toml + regstack.secrets.env."
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
    "--port",
    type=int,
    default=None,
    help="Pin the wizard server's TCP port (default: random free port).",
)
@click.option(
    "--headless",
    is_flag=True,
    help=(
        "Don't open a GUI. Validate from CLI flags, write the config, "
        "print the diff. For CI and headless hosts."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Implies --headless. Print the diff but do not write the files.",
)
@click.option(
    "--print-only",
    is_flag=True,
    help="DEPRECATED: alias for --headless.",
)
@click.option("--region", default=None, help="Used only with --headless / --dry-run.")
@click.option("--from-address", default=None, help="Used only with --headless / --dry-run.")
@click.option(
    "--credential-source",
    type=click.Choice(list(CREDENTIAL_SOURCES)),
    default="chain",
    show_default=True,
    help="Used only with --headless / --dry-run.",
)
@click.option(
    "--profile", default=None, help="Used only with --headless / --dry-run (profile mode)."
)
@click.option(
    "--access-key-id", default=None, help="Used only with --headless / --dry-run (explicit mode)."
)
@click.option(
    "--secret-access-key",
    default=None,
    help="Used only with --headless / --dry-run (explicit mode).",
)
def setup(
    config_path_in: Path | None,
    target_path_in: Path | None,
    port: int | None,
    headless: bool,
    dry_run: bool,
    print_only: bool,
    region: str | None,
    from_address: str | None,
    credential_source: str,
    profile: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
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
            region=region or "",
            from_address=from_address or "",
            credential_source=credential_source,
            profile=profile,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            dry_run=dry_run,
        )
        return

    _run_gui(target_dir=target_dir, port=port)


def _run_headless(
    *,
    target_dir: Path,
    region: str,
    from_address: str,
    credential_source: str,
    profile: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    dry_run: bool,
) -> None:
    if region and region not in KNOWN_SES_REGIONS:
        click.echo(
            f"error: {region!r} is not a known SES region. Try us-east-1, eu-west-1, etc.",
            err=True,
        )
        sys.exit(2)
    inputs = {
        "existing_ses": False,
        "replace_existing": True,
        "ses_region": region,
        "from_address": from_address,
        "credential_source": credential_source,
        "ses_profile": profile,
        "ses_access_key_id": access_key_id,
        "ses_secret_access_key": secret_access_key,
        # Print-only mode skips the AWS state checks; the validator
        # respects this by treating sandbox as not-attested-not-detected.
        "aws_in_sandbox": False,
        "sandbox_attested": True,
        "skip_test_send": True,
    }
    result = validate_all(inputs)
    if not result.ok:
        click.echo("Validation failed:", err=True)
        for err in result.errors:
            click.echo(f"  - {err.field}: {err.message}", err=True)
        sys.exit(2)

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    write_result = merge_into_config(
        target_dir=target_dir,
        ses_region=region,
        from_address=from_address,
        credential_source=credential_source,  # type: ignore[arg-type]
        ses_profile=profile,
        ses_access_key_id=access_key_id,
        ses_secret_access_key=secret_access_key,
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


def _run_gui(*, target_dir: Path, port: int | None) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / "regstack.toml"
    existing_from_address = _existing_from_address(config_path)
    server = make_wizard_server(
        target_dir=target_dir,
        existing_from_address=existing_from_address,
        port=port,
    )
    click.echo(f"Wizard URL: {server.url}")
    click.echo("Opening wizard window… close it to exit.")

    from regstack.wizard.ses.window import WizardWindowError, open_wizard_window

    try:
        run_windowed(server, open_wizard_window)
    except WizardWindowError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


def _existing_from_address(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    try:
        import tomllib

        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    email = data.get("email")
    if not isinstance(email, dict):
        return None
    value = email.get("from_address")
    return value if isinstance(value, str) and value else None


__all__ = ["ses", "setup"]
