"""Uvicorn launcher for the SES setup wizard's FastAPI app.

Mirrors :mod:`regstack.wizard.oauth_google.server` — same launch
token, same loopback binding, same shutdown discipline. The test
suite drives the underlying app directly via
:class:`fastapi.testclient.TestClient` without going through this
module.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from regstack.wizard._shutdown import new_shutdown_event, wait_for_shutdown
from regstack.wizard.ses.routes import WizardSettings, build_wizard_app


@dataclass(slots=True)
class WizardServer:
    host: str
    port: int
    launch_token: str
    url: str
    settings: WizardSettings


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_wizard_server(
    *,
    target_dir: Path,
    existing_from_address: str | None = None,
    port: int | None = None,
) -> WizardServer:
    bound_port = port or find_free_port()
    token = secrets.token_urlsafe(32)
    settings = WizardSettings(
        target_dir=target_dir,
        launch_token=token,
        shutdown_event=new_shutdown_event(),
        existing_from_address=existing_from_address,
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
        await wait_for_shutdown(server.settings.shutdown_event)
    finally:
        uv.should_exit = True
        await serve_task


__all__ = [
    "WizardServer",
    "find_free_port",
    "make_wizard_server",
    "serve",
]
