"""pywebview launcher for the SES setup wizard.

Identical contract to :mod:`regstack.wizard.oauth_google.window`,
just with a different window title.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regstack.wizard.ses.server import WizardServer


class WizardWindowError(RuntimeError):
    """Raised when pywebview can't open a window on this host."""


def open_wizard_window(server: WizardServer, title: str = "regstack — SES setup") -> None:
    try:
        import webview
    except Exception as exc:  # pragma: no cover — depends on host
        raise WizardWindowError(
            "pywebview could not be imported. The SES setup wizard "
            "requires a desktop environment with a webview backend "
            "(WebKit on macOS, GTK/QtWebEngine on Linux, Edge "
            "WebView2 on Windows). Run `regstack ses setup --print-only` "
            "instead if you're on a headless host."
        ) from exc

    window = webview.create_window(title, server.url, width=820, height=720)
    if window is None:  # pragma: no cover — pywebview always returns a Window in practice
        raise WizardWindowError("pywebview did not return a window handle.")

    def _watch_shutdown() -> None:
        # A plain blocking wait: shutdown_event is a threading.Event, so this
        # needs no event loop. Spinning one up here (asyncio.run) is what
        # raised "bound to a different event loop" — the Event already
        # belonged to uvicorn's loop in the server thread.
        try:
            server.settings.shutdown_event.wait()
        finally:
            with contextlib.suppress(Exception):
                window.destroy()

    threading.Thread(target=_watch_shutdown, daemon=True).start()
    try:
        webview.start()
    except Exception as exc:  # pragma: no cover — host-specific
        raise WizardWindowError(
            f"pywebview failed to start: {exc}. The SES setup wizard requires a desktop session."
        ) from exc
    finally:
        server.settings.shutdown_event.set()


__all__ = ["WizardWindowError", "open_wizard_window"]
