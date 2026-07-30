"""pywebview launcher for the theme designer.

A thin adapter over :func:`regstack.wizard._scaffold.open_window`, which
holds the window and shutdown-watcher mechanics shared with the OAuth and
SES wizards. What stays here is the designer's own error type — its CLI
catches that specific exception, and tests import it by name — plus the
wider default window, since the designer shows a live preview alongside
its controls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.wizard._scaffold import WizardWindowError as _BaseWindowError
from regstack.wizard._scaffold import open_window

if TYPE_CHECKING:
    from regstack.wizard._scaffold import WizardServer

_DESIGNER_WIDTH = 1100


class DesignerWindowError(_BaseWindowError):
    """Raised when pywebview can't open a window on this host."""


def open_designer_window(
    server: WizardServer,
    title: str = "regstack — theme designer",
) -> None:
    """Open a native webview at ``server.url`` and run the GUI loop.

    Blocks until the user closes the window OR the server's
    ``shutdown_event`` fires (e.g. the SPA POSTed to ``/api/done``).

    Raises:
        DesignerWindowError: pywebview is missing or no GUI backend is
            available (typical on a headless server).
    """
    open_window(
        server,
        title=title,
        product="The theme designer",
        print_only_command="regstack theme design --print-only",
        error_cls=DesignerWindowError,
        width=_DESIGNER_WIDTH,
    )


__all__ = ["DesignerWindowError", "open_designer_window"]
