from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click

from regstack.cli._runtime import open_regstack


@click.command(
    name="create-admin",
    help="Create or promote a superuser. Idempotent — re-running flips an existing user to admin.",
)
@click.option("--email", required=True, help="Admin email address.")
@click.option(
    "--password",
    default=None,
    help="Password. If omitted you will be prompted (with confirmation).",
)
@click.option(
    "--config",
    "toml_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to regstack.toml (default: search cwd / $REGSTACK_CONFIG).",
)
def create_admin(email: str, password: str | None, toml_path: Path | None) -> None:
    if password is None:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < 8:
        raise click.UsageError("Password must be at least 8 characters.")

    asyncio.run(_run(email=email, password=password, toml_path=toml_path))


async def _run(*, email: str, password: str, toml_path: Path | None) -> None:
    async with open_regstack(toml_path) as rs:
        user = await rs.bootstrap_admin(email, password)
        verb = "promoted to admin" if user.is_superuser else "created"
        click.echo(
            click.style(f"User {user.email} {verb} (id={user.id}).", fg="green"),
            file=sys.stderr,
        )
