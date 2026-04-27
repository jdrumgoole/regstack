from __future__ import annotations

import click

from regstack.cli.admin import create_admin as create_admin_cmd
from regstack.cli.doctor import doctor as doctor_cmd
from regstack.cli.init import init as init_cmd
from regstack.version import __version__


@click.group(help="regstack — embeddable account registration for FastAPI/MongoDB apps.")
@click.version_option(__version__, prog_name="regstack")
def cli() -> None:
    pass


cli.add_command(init_cmd, name="init")
cli.add_command(create_admin_cmd)
cli.add_command(doctor_cmd)


def main() -> None:
    cli(prog_name="regstack")


if __name__ == "__main__":
    main()
