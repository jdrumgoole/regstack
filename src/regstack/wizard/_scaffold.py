"""Shared machinery for the pywebview setup wizards.

`regstack oauth setup`, `regstack theme design` and `regstack ses setup`
are the same program three times over: mint a launch token, bind uvicorn
to a free loopback port, open a native window at that URL, and tear both
down when either side finishes. Only the FastAPI app behind it, the
window's title and width, and the wizard-specific settings fields really
differ — measured before this refactor, `server.py` was 42 lines of code
with 4-6 differing between wizards and `window.py` 32 lines with 6-8.

That duplication had a cost, not just a smell: the `asyncio.Event`
shutdown bug fixed in 0.9.1 was one defect that shipped as three,
because each wizard carried its own copy of the same watcher thread.
`CLAUDE.md` said to consolidate once a third wizard landed. It did (SES),
and then the bug arrived, so this is that consolidation.

What stays per-wizard: the settings dataclass (each has genuinely
different fields), the FastAPI app and its routes, the CLI's own options,
and the window's public error type — the CLI catches a specific one and
tests import it by name.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import socket
import threading
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import uvicorn

from regstack.wizard._shutdown import wait_for_shutdown

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI

_LAUNCH_TOKEN_BYTES = 32


class WizardWindowError(RuntimeError):
    """Base for "this host can't open a wizard window".

    Each wizard subclasses it so its CLI can catch its own type, and so
    the message can name the right command to fall back to.
    """


class HasShutdownEvent(Protocol):
    """The only thing the shared scaffold needs from a settings object."""

    shutdown_event: threading.Event


def new_launch_token() -> str:
    """Return the random token the wizard's browser must present."""
    return secrets.token_urlsafe(_LAUNCH_TOKEN_BYTES)


def find_free_port() -> int:
    """Return a free TCP port on ``127.0.0.1``.

    Uses ``SO_REUSEADDR``; uvicorn binds the same port immediately after,
    so the kernel-level race window is microseconds. Acceptable for a
    single-user local-only flow.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(s.getsockname()[1])


@dataclass(slots=True)
class WizardServer:
    """A running (or about-to-run) wizard server.

    Attributes:
        host: Always ``127.0.0.1``. Stored explicitly for clarity.
        port: TCP port uvicorn binds to.
        launch_token: Random URL-safe token the browser must present.
        url: The full SPA URL with the token in the query string. Hand
            this to :func:`webview.create_window`.
        settings: The wizard's own settings object, injected into its app.
        app_factory: Builds the FastAPI app from ``settings``. Held here
            so :func:`serve` needs no per-wizard branching.
    """

    host: str
    port: int
    launch_token: str
    url: str
    settings: Any
    app_factory: Callable[[Any], FastAPI]


def assemble_server(
    *,
    settings: HasShutdownEvent,
    app_factory: Callable[[Any], FastAPI],
    launch_token: str,
    port: int | None = None,
) -> WizardServer:
    """Bind a port and compose the descriptor. Starts nothing.

    The token is minted by the caller because it has to go into the
    wizard's own settings object before this is called, and each wizard
    constructs its settings differently.
    """
    bound_port = port or find_free_port()
    return WizardServer(
        host="127.0.0.1",
        port=bound_port,
        launch_token=launch_token,
        url=f"http://127.0.0.1:{bound_port}/?token={launch_token}",
        settings=settings,
        app_factory=app_factory,
    )


async def serve(server: WizardServer) -> None:
    """Run uvicorn until ``server.settings.shutdown_event`` is set.

    The event is a :class:`threading.Event`, so it is awaited through
    :func:`regstack.wizard._shutdown.wait_for_shutdown` rather than
    directly — see that module for why an ``asyncio.Event`` cannot work
    across the wizard's three threads.
    """
    config = uvicorn.Config(
        server.app_factory(server.settings),
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


def open_window(
    server: WizardServer,
    *,
    title: str,
    product: str,
    print_only_command: str,
    error_cls: type[WizardWindowError] = WizardWindowError,
    width: int = 820,
    height: int = 720,
) -> None:
    """Open a native webview at ``server.url`` and run the GUI loop.

    Blocks until the user closes the window OR the server's
    ``shutdown_event`` fires (e.g. the SPA POSTed to ``/api/done``).

    Args:
        server: The descriptor whose ``url`` the window loads.
        title: Native window title.
        product: Human name used in error messages ("the theme designer").
        print_only_command: The headless fallback to suggest on failure.
        error_cls: The wizard's own error subclass, so its CLI can catch
            a specific type.
        width: Window width; the theme designer wants more room.
        height: Window height.

    Raises:
        error_cls: pywebview is missing, or no GUI backend is available
            (typical on a headless server).
    """
    try:
        import webview
    except Exception as exc:  # pragma: no cover — depends on host
        raise error_cls(
            f"pywebview could not be imported. {product} requires a desktop "
            "environment with a webview backend (WebKit on macOS, "
            "GTK/QtWebEngine on Linux, Edge WebView2 on Windows). Run "
            f"`{print_only_command}` instead if you're on a headless host."
        ) from exc

    window = webview.create_window(title, server.url, width=width, height=height)
    if window is None:  # pragma: no cover — pywebview always returns a Window
        raise error_cls("pywebview did not return a window handle.")

    def _watch_shutdown() -> None:
        # A plain blocking wait: shutdown_event is a threading.Event, so this
        # needs no event loop. Spinning one up here (asyncio.run) is what
        # raised "bound to a different event loop" before 0.9.1 — the Event
        # already belonged to uvicorn's loop in the server thread.
        try:
            server.settings.shutdown_event.wait()
        finally:
            with contextlib.suppress(Exception):  # window may already be gone
                window.destroy()

    threading.Thread(target=_watch_shutdown, daemon=True).start()
    try:
        webview.start()
    except Exception as exc:  # pragma: no cover — host-specific
        raise error_cls(
            f"pywebview failed to start: {exc}. {product} requires a desktop session."
        ) from exc
    finally:
        # Window closed → tell the server to stop too.
        server.settings.shutdown_event.set()


def run_windowed(
    server: WizardServer,
    open_window_fn: Callable[[WizardServer], None],
    *,
    join_timeout: float = 5.0,
) -> None:
    """Run the server in a thread, the window on this thread, then tear down.

    pywebview must own the main thread on macOS, so uvicorn goes to a
    background thread. That thread's event loop is private to it: nothing
    out here may touch an asyncio primitive belonging to it, which is why
    shutdown crosses the boundary via the ``threading.Event`` in
    ``server.settings``.

    Re-raises whatever ``open_window_fn`` raises, after signalling
    shutdown and joining, so the caller's error handling still runs.
    """
    thread = threading.Thread(target=lambda: asyncio.run(serve(server)), daemon=True)
    thread.start()
    try:
        open_window_fn(server)
    finally:
        server.settings.shutdown_event.set()
        thread.join(timeout=join_timeout)


__all__ = [
    "HasShutdownEvent",
    "WizardServer",
    "WizardWindowError",
    "assemble_server",
    "find_free_port",
    "new_launch_token",
    "open_window",
    "run_windowed",
    "serve",
]
