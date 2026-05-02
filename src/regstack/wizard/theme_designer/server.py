"""Uvicorn launcher for the theme designer.

Mirrors :mod:`regstack.wizard.oauth_google.server` — same free-port
discovery, same launch-token mint, same shutdown-event lifecycle.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from regstack.wizard.theme_designer.routes import (
    DesignerSettings,
    build_designer_app,
)


@dataclass(slots=True)
class DesignerServer:
    host: str
    port: int
    launch_token: str
    url: str
    settings: DesignerSettings


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_designer_server(
    *,
    target_dir: Path,
    port: int | None = None,
    filename: str | None = None,
) -> DesignerServer:
    bound_port = port or find_free_port()
    token = secrets.token_urlsafe(32)
    settings = DesignerSettings(
        target_dir=target_dir,
        launch_token=token,
        shutdown_event=asyncio.Event(),
        **({"filename": filename} if filename else {}),
    )
    url = f"http://127.0.0.1:{bound_port}/?token={token}"
    return DesignerServer(
        host="127.0.0.1",
        port=bound_port,
        launch_token=token,
        url=url,
        settings=settings,
    )


async def serve(server: DesignerServer) -> None:
    """Run uvicorn until ``server.settings.shutdown_event`` is set."""
    app = build_designer_app(server.settings)
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
    "DesignerServer",
    "find_free_port",
    "make_designer_server",
    "serve",
]
