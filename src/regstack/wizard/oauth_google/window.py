"""pywebview launcher for the OAuth setup wizard.

A thin adapter over :func:`regstack.wizard._scaffold.open_window`, which
holds the window and shutdown-watcher mechanics shared with the theme
designer and the SES wizard. What stays here is this wizard's own error
type — its CLI catches that specific exception, and tests import it by
name — plus the strings that name the product and its headless fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.wizard._scaffold import WizardWindowError as _BaseWindowError
from regstack.wizard._scaffold import open_window

if TYPE_CHECKING:
    from regstack.wizard._scaffold import WizardServer


class WizardWindowError(_BaseWindowError):
    """Raised when pywebview can't open a window on this host."""


def open_wizard_window(server: WizardServer, title: str = "regstack — Google OAuth setup") -> None:
    """Open a native webview at ``server.url`` and run the GUI loop.

    Blocks until the user closes the window OR the server's
    ``shutdown_event`` fires (e.g. the SPA POSTed to ``/api/done``).

    Raises:
        WizardWindowError: pywebview is missing or no GUI backend is
            available (typical on a headless server).
    """
    open_window(
        server,
        title=title,
        product="The OAuth setup wizard",
        print_only_command="regstack oauth setup --print-only",
        error_cls=WizardWindowError,
    )


__all__ = ["WizardWindowError", "open_wizard_window"]
