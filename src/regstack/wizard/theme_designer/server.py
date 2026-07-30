"""Uvicorn launcher for the theme designer's local-only FastAPI app.

The mechanics — free loopback port, launch token, uvicorn lifecycle,
shutdown discipline — live in :mod:`regstack.wizard._scaffold` and are
shared with the OAuth and SES wizards. This module supplies only what's
specific to the designer: its settings fields and its app.

``DesignerServer`` is an alias of the shared ``WizardServer``, kept
because the CLI and tests refer to it by that name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.wizard._scaffold import (
    WizardServer as DesignerServer,
)
from regstack.wizard._scaffold import (
    assemble_server,
    find_free_port,
    new_launch_token,
    serve,
)
from regstack.wizard._shutdown import new_shutdown_event
from regstack.wizard.theme_designer.routes import (
    DesignerSettings,
    build_designer_app,
)

if TYPE_CHECKING:
    from pathlib import Path


def make_designer_server(
    *,
    target_dir: Path,
    port: int | None = None,
    filename: str | None = None,
) -> DesignerServer:
    """Build the :class:`DesignerServer` descriptor (does not start it)."""
    token = new_launch_token()
    settings = DesignerSettings(
        target_dir=target_dir,
        launch_token=token,
        shutdown_event=new_shutdown_event(),
        **({"filename": filename} if filename else {}),
    )
    return assemble_server(
        settings=settings,
        app_factory=build_designer_app,
        launch_token=token,
        port=port,
    )


__all__ = [
    "DesignerServer",
    "find_free_port",
    "make_designer_server",
    "serve",
]
