"""Uvicorn launcher for the SES setup wizard's FastAPI app.

The mechanics — free loopback port, launch token, uvicorn lifecycle,
shutdown discipline — live in :mod:`regstack.wizard._scaffold` and are
shared with the OAuth wizard and the theme designer. This module supplies
only what's specific to this wizard: its settings fields and its app.

The test suite drives the underlying app directly via
:class:`fastapi.testclient.TestClient` without going through this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.wizard._scaffold import (
    WizardServer,
    assemble_server,
    find_free_port,
    new_launch_token,
    serve,
)
from regstack.wizard._shutdown import new_shutdown_event
from regstack.wizard.ses.routes import WizardSettings, build_wizard_app

if TYPE_CHECKING:
    from pathlib import Path


def make_wizard_server(
    *,
    target_dir: Path,
    existing_from_address: str | None = None,
    port: int | None = None,
) -> WizardServer:
    """Build the :class:`WizardServer` descriptor (does not start it)."""
    token = new_launch_token()
    settings = WizardSettings(
        target_dir=target_dir,
        launch_token=token,
        shutdown_event=new_shutdown_event(),
        existing_from_address=existing_from_address,
    )
    return assemble_server(
        settings=settings,
        app_factory=build_wizard_app,
        launch_token=token,
        port=port,
    )


__all__ = [
    "WizardServer",
    "find_free_port",
    "make_wizard_server",
    "serve",
]
