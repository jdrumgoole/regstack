from __future__ import annotations

import click

from regstack.cli.admin import create_admin as create_admin_cmd
from regstack.cli.doctor import doctor as doctor_cmd
from regstack.cli.init import init as init_cmd
from regstack.cli.migrate import migrate as migrate_cmd
from regstack.version import __version__


class _LazyOauthGroup(click.Group):
    """Defer wizard imports until ``regstack oauth …`` is actually run.

    Importing the wizard pulls in pywebview, uvicorn, and tomlkit. We
    don't want to pay that on every ``regstack init`` / ``regstack doctor``
    invocation. Click's :meth:`Group.get_command` is the seam.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        return ["setup"]

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        if name != "setup":
            return None
        from regstack.wizard.oauth_google.cli import setup as setup_cmd

        return setup_cmd


@click.group(help="regstack — embeddable account registration for FastAPI apps.")
@click.version_option(__version__, prog_name="regstack")
def cli() -> None:
    pass


cli.add_command(init_cmd, name="init")
cli.add_command(create_admin_cmd)
cli.add_command(doctor_cmd)
cli.add_command(migrate_cmd)
cli.add_command(_LazyOauthGroup(name="oauth", help="OAuth provider setup wizards."))


def main() -> None:
    cli(prog_name="regstack")


if __name__ == "__main__":
    main()
