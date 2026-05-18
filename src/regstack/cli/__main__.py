from __future__ import annotations

import click

from regstack.cli.admin import create_admin as create_admin_cmd
from regstack.cli.doctor import doctor as doctor_cmd
from regstack.cli.init import init as init_cmd
from regstack.cli.migrate import migrate as migrate_cmd
from regstack.cli.validate.cli import validate as validate_cmd
from regstack.version import __version__

_EXTRA_BLURB = {
    "wizard": "'wizard' extra (pywebview + tomlkit + uvicorn)",
    "ses": "'ses' extra (aioboto3 for AWS API calls)",
    "oauth": "'oauth' extra (pyjwt[crypto] for OAuth ID-token verification)",
}


def _missing_extra(subcommand: str, *extras: str) -> click.Command:
    """Click command that prints a clear install hint and exits non-zero.

    Used as the fallback when a wizard subcommand is invoked but one or
    more of the required optional extras is not installed. Gives a
    one-line actionable message instead of an ImportError traceback
    from deep inside the wizard package.

    Args:
        subcommand: Display name of the subcommand (e.g.
            ``"regstack ses setup"``). Used in the error message.
        *extras: One or more extra names the subcommand requires. The
            hint instructs the operator to install all of them
            together via a single ``pip install regstack[a,b,...]``.
    """
    name = subcommand.split()[-1]
    blurbs = ", ".join(_EXTRA_BLURB.get(x, f"'{x}' extra") for x in extras)
    extras_arg = ",".join(extras)
    hint = (
        f"The `{subcommand}` command requires the {blurbs}. "
        f"Install with `pip install 'regstack[{extras_arg}]'` "
        f"or `uv sync {' '.join(f'--extra {x}' for x in extras)}`."
    )

    @click.command(name=name, help=f"(needs `regstack[{extras_arg}]`)")
    def _stub() -> None:
        click.echo(hint, err=True)
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
            return _missing_extra("regstack oauth setup", "wizard")
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
            return _missing_extra("regstack theme design", "wizard")
        return design_cmd


class _LazySesGroup(click.Group):
    """Same lazy-import pattern for the SES setup wizard.

    Needs BOTH ``wizard`` (pywebview, uvicorn, tomlkit for the SPA
    shell) AND ``ses`` (aioboto3 for AWS API calls). The hint surfaces
    the joint install command so an operator missing either gets a
    single actionable fix.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        return ["setup"]

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        if name != "setup":
            return None
        try:
            from regstack.wizard.ses.cli import setup as setup_cmd
        except ImportError:
            return _missing_extra("regstack ses setup", "wizard", "ses")
        return setup_cmd


@click.group(help="regstack — embeddable account registration for FastAPI apps.")
@click.version_option(__version__, prog_name="regstack")
def cli() -> None:
    pass


cli.add_command(init_cmd, name="init")
cli.add_command(create_admin_cmd)
cli.add_command(doctor_cmd)
cli.add_command(migrate_cmd)
cli.add_command(validate_cmd)
cli.add_command(_LazyOauthGroup(name="oauth", help="OAuth provider setup wizards."))
cli.add_command(_LazyThemeGroup(name="theme", help="Theme designer for the SSR pages."))
cli.add_command(_LazySesGroup(name="ses", help="SES email backend setup wizard."))


def main() -> None:
    cli(prog_name="regstack")


if __name__ == "__main__":
    main()
