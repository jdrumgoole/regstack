"""Uvicorn launcher for the OAuth wizard's local-only FastAPI app.

The mechanics — free loopback port, launch token, uvicorn lifecycle,
shutdown discipline — live in :mod:`regstack.wizard._scaffold` and are
shared with the theme designer and the SES wizard. This module supplies
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
from regstack.wizard.oauth_google.routes import WizardSettings, build_wizard_app

if TYPE_CHECKING:
    from pathlib import Path


def make_wizard_server(
    *,
    target_dir: Path,
    api_prefix: str = "/api/auth",
    existing_base_url: str | None = None,
    port: int | None = None,
) -> WizardServer:
    """Build the :class:`WizardServer` descriptor (does not start it).

    The settings object embedded in the returned descriptor is the same
    one wired into the FastAPI app, so signalling
    ``settings.shutdown_event`` from anywhere stops the loop.
    """
    token = new_launch_token()
    settings = WizardSettings(
        target_dir=target_dir,
        api_prefix=api_prefix,
        launch_token=token,
        shutdown_event=new_shutdown_event(),
        existing_base_url=existing_base_url,
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
