"""Uvicorn launcher for the wizard's local-only FastAPI app.

Used by the CLI / pywebview launcher; the test suite drives the
underlying app directly via :class:`fastapi.testclient.TestClient`
without going through this module.

The server binds ``127.0.0.1`` (never ``0.0.0.0``) on a free random
port discovered ahead of time, so the pywebview window can be told
the URL before uvicorn finishes binding. The launch token is
generated here and passed into the FastAPI app via
:class:`~regstack.wizard.oauth_google.routes.WizardSettings`.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from regstack.wizard.oauth_google.routes import WizardSettings, build_wizard_app


@dataclass(slots=True)
class WizardServer:
    """A running (or about-to-run) wizard server.

    Attributes:
        host: Always ``127.0.0.1``. Stored explicitly for clarity.
        port: TCP port uvicorn binds to.
        launch_token: Random URL-safe token the browser must present.
        url: Convenience — the full SPA URL with the token in the
            query string. Hand this to :func:`webview.create_window`.
        settings: The :class:`WizardSettings` injected into the app.
    """

    host: str
    port: int
    launch_token: str
    url: str
    settings: WizardSettings


def find_free_port() -> int:
    """Return a free TCP port on ``127.0.0.1``.

    Uses ``SO_REUSEADDR``; uvicorn binds the same port immediately
    after, so the kernel-level race window is microseconds. Acceptable
    for a single-user local-only flow.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_wizard_server(
    *,
    target_dir: Path,
    api_prefix: str = "/api/auth",
    existing_base_url: str | None = None,
    port: int | None = None,
) -> WizardServer:
    """Build the :class:`WizardServer` descriptor (does not start it).

    The settings object embedded in the returned descriptor is the
    same one wired into the FastAPI app, so signalling
    ``settings.shutdown_event`` from anywhere stops the loop.
    """
    bound_port = port or find_free_port()
    token = secrets.token_urlsafe(32)
    settings = WizardSettings(
        target_dir=target_dir,
        api_prefix=api_prefix,
        launch_token=token,
        shutdown_event=asyncio.Event(),
        existing_base_url=existing_base_url,
    )
    url = f"http://127.0.0.1:{bound_port}/?token={token}"
    return WizardServer(
        host="127.0.0.1",
        port=bound_port,
        launch_token=token,
        url=url,
        settings=settings,
    )


async def serve(server: WizardServer) -> None:
    """Run uvicorn until ``server.settings.shutdown_event`` is set.

    Used by the CLI's launcher. Tests bypass this and drive the app
    directly via :class:`TestClient`.
    """
    app = build_wizard_app(server.settings)
    config = uvicorn.Config(
        app,
        host=server.host,
        port=server.port,
        log_level="warning",
        access_log=False,
    )
    uv = uvicorn.Server(config)

    serve_task = asyncio.create_task(uv.serve())
    try:
        await server.settings.shutdown_event.wait()
    finally:
        uv.should_exit = True
        await serve_task


__all__ = [
    "WizardServer",
    "find_free_port",
    "make_wizard_server",
    "serve",
]
