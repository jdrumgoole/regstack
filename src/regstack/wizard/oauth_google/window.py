"""pywebview launcher for the OAuth setup wizard.

Opens a native window pointed at the local wizard server. Closing the
window signals the server to shut down. This module is a thin shim
over :mod:`webview` and is the only place ``import webview`` lives —
keeping it isolated lets the CLI surface a clean error when the host
machine can't open a GUI (e.g. headless CI).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regstack.wizard.oauth_google.server import WizardServer


class WizardWindowError(RuntimeError):
    """Raised when pywebview can't open a window on this host."""


def open_wizard_window(server: WizardServer, title: str = "regstack — Google OAuth setup") -> None:
    """Open a native webview at ``server.url`` and run the GUI loop.

    Blocks until the user closes the window OR the server's
    ``shutdown_event`` fires (e.g. the SPA POSTed to ``/api/done``).

    Raises:
        WizardWindowError: pywebview is missing or no GUI backend is
            available (typical on a headless server).
    """
    try:
        import webview
    except Exception as exc:  # pragma: no cover — depends on host
        raise WizardWindowError(
            "pywebview could not be imported. The OAuth setup wizard "
            "requires a desktop environment with a webview backend "
            "(WebKit on macOS, GTK/QtWebEngine on Linux, Edge "
            "WebView2 on Windows). Run `regstack oauth setup --print-only` "
            "instead if you're on a headless host."
        ) from exc

    window = webview.create_window(title, server.url, width=820, height=720)
    if window is None:  # pragma: no cover — pywebview always returns a Window in practice
        raise WizardWindowError("pywebview did not return a window handle.")

    def _watch_shutdown() -> None:
        async def _wait() -> None:
            await server.settings.shutdown_event.wait()

        try:
            asyncio.run(_wait())
        finally:
            with contextlib.suppress(Exception):
                window.destroy()

    threading.Thread(target=_watch_shutdown, daemon=True).start()
    try:
        webview.start()
    except Exception as exc:  # pragma: no cover — host-specific
        raise WizardWindowError(
            f"pywebview failed to start: {exc}. The OAuth setup wizard requires a desktop session."
        ) from exc
    finally:
        # Window closed → tell the server to stop too.
        server.settings.shutdown_event.set()


__all__ = ["WizardWindowError", "open_wizard_window"]
