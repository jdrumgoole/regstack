"""pywebview launcher for the theme designer.

Mirrors :mod:`regstack.wizard.oauth_google.window`. Same isolation —
the only place ``import webview`` lives in the designer subtree.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regstack.wizard.theme_designer.server import DesignerServer


class DesignerWindowError(RuntimeError):
    """Raised when pywebview can't open a window on this host."""


def open_designer_window(
    server: DesignerServer,
    title: str = "regstack — theme designer",
) -> None:
    """Open a native webview at ``server.url`` and run the GUI loop."""
    try:
        import webview
    except Exception as exc:  # pragma: no cover — depends on host
        raise DesignerWindowError(
            "pywebview could not be imported. The theme designer "
            "requires a desktop environment with a webview backend "
            "(WebKit on macOS, GTK / QtWebEngine on Linux, Edge "
            "WebView2 on Windows). Run `regstack theme design "
            "--print-only` instead if you're on a headless host."
        ) from exc

    window = webview.create_window(title, server.url, width=1100, height=720)
    if window is None:  # pragma: no cover
        raise DesignerWindowError("pywebview did not return a window handle.")

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
        raise DesignerWindowError(
            f"pywebview failed to start: {exc}. The theme designer requires a desktop session."
        ) from exc
    finally:
        server.settings.shutdown_event.set()


__all__ = ["DesignerWindowError", "open_designer_window"]
