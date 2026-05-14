from __future__ import annotations

import click

from regstack.cli.admin import create_admin as create_admin_cmd
from regstack.cli.doctor import doctor as doctor_cmd
from regstack.cli.init import init as init_cmd
from regstack.cli.migrate import migrate as migrate_cmd
from regstack.version import __version__

_WIZARD_EXTRA_HINT = (
    "The {subcommand} command requires the optional 'wizard' extra "
    "(pywebview + tomlkit + uvicorn). Install with "
    "`pip install regstack[wizard]` or `uv sync --extra wizard`."
)


def _missing_wizard_extra(subcommand: str) -> click.Command:
    """Click command that prints a clear install hint and exits non-zero.

    Used as the fallback when a wizard subcommand is invoked but the
    ``wizard`` extra isn't installed — gives a one-line actionable
    message instead of an ImportError traceback from deep inside the
    wizard package.
    """
    name = subcommand.split()[-1]

    @click.command(name=name, help="(needs `regstack[wizard]`)")
    def _stub() -> None:
        click.echo(_WIZARD_EXTRA_HINT.format(subcommand=f"`{subcommand}`"), err=True)
        raise SystemExit(2)

    return _stub


class _LazyOauthGroup(click.Group):
    """Defer wizard imports until ``regstack oauth …`` is actually run.

    Importing the wizard pulls in pywebview, uvicorn, and tomlkit (the
    ``wizard`` optional extra). Hosts who don't use the GUI tools
    don't pay for those deps at install time; the cost is that
    running a wizard subcommand without the extra exits with a clear
    install hint instead of an ImportError.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        return ["setup"]

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        if name != "setup":
            return None
        try:
            from regstack.wizard.oauth_google.cli import setup as setup_cmd
        except ImportError:
            return _missing_wizard_extra("regstack oauth setup")
        return setup_cmd


class _LazyThemeGroup(click.Group):
    """Same lazy-import pattern for the theme designer subtree."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return ["design"]

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        if name != "design":
            return None
        try:
            from regstack.wizard.theme_designer.cli import design as design_cmd
        except ImportError:
            return _missing_wizard_extra("regstack theme design")
        return design_cmd


@click.group(help="regstack — embeddable account registration for FastAPI apps.")
@click.version_option(__version__, prog_name="regstack")
def cli() -> None:
    pass


cli.add_command(init_cmd, name="init")
cli.add_command(create_admin_cmd)
cli.add_command(doctor_cmd)
cli.add_command(migrate_cmd)
cli.add_command(_LazyOauthGroup(name="oauth", help="OAuth provider setup wizards."))
cli.add_command(_LazyThemeGroup(name="theme", help="Theme designer for the SSR pages."))


def main() -> None:
    cli(prog_name="regstack")


if __name__ == "__main__":
    main()
