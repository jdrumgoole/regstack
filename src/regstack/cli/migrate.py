from __future__ import annotations

import sys
from pathlib import Path

import click

from regstack.backends.factory import detect_backend_kind
from regstack.cli._paths import resolve_toml_path
from regstack.cli._runtime import load_runtime_config


@click.command(
    name="migrate",
    help="Run the bundled Alembic migrations against the configured database_url.",
)
@click.option(
    "--config",
    "config_path_in",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "Path to regstack.toml (or a directory containing it). "
        "Default: search cwd / $REGSTACK_CONFIG."
    ),
)
@click.option(
    "--target",
    default="head",
    show_default=True,
    help=(
        "Alembic revision to upgrade to (e.g. 'head', '0001', '+1'). "
        "Note: distinct from the global --target/--config flag pair on "
        "config-writing commands."
    ),
)
def migrate(config_path_in: Path | None, target: str) -> None:
    toml_path = resolve_toml_path(config_path_in)
    """Idempotent: re-running on a DB at the target revision is a no-op.

    Mongo backends are silently skipped — TTL indexes are installed by
    `regstack.backends.mongo.install_indexes` (called from
    `RegStack.install_schema`) on every app start, so there's no
    separate migration story.
    """
    config = load_runtime_config(toml_path)
    url = config.database_url.get_secret_value()
    kind = detect_backend_kind(url)
    if kind.value == "mongo":
        click.echo(
            click.style(
                "skip: mongo backends use install_schema() at app start; nothing to migrate.",
                fg="yellow",
            )
        )
        return

    from regstack.backends.sql.migrations import current, head_revision, upgrade

    before = current(url)
    head = head_revision()
    if before == target or (target == "head" and before == head):
        click.echo(click.style(f"already at {target} ({head})", fg="green"))
        return

    click.echo(f"upgrading {url} from {before or '(empty)'} to {target}…")
    try:
        upgrade(url, target)
    except Exception as exc:  # surface alembic errors with a non-zero exit
        click.echo(click.style(f"upgrade failed: {exc}", fg="red"), err=True)
        sys.exit(1)
    after = current(url)
    click.echo(click.style(f"now at {after}", fg="green"))
