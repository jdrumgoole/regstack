"""Cross-thread shutdown signalling for the pywebview wizards.

Every wizard runs three threads at once: pywebview owns the main thread
(a macOS requirement), uvicorn runs in a background thread with its own
event loop, and a watcher thread destroys the window when the server
stops. The shutdown signal has to cross all three.

``asyncio.Event`` cannot do that. It binds to the first running loop that
touches it and raises ``RuntimeError: ... is bound to a different event
loop`` for any other, which is what ``regstack oauth setup`` did on
startup: the watcher's ``asyncio.run()`` created a second loop and awaited
the Event that uvicorn's loop already owned. ``Event.set()`` from a
non-owning thread is unsafe in the same way — it resolves futures
belonging to another loop — so signalling shutdown from the CLI or the
window was never reliable either.

``threading.Event`` is thread-safe by construction and belongs to no
loop, so it is the right primitive for this shape. The only piece that
needs care is awaiting one from inside a coroutine, which
:func:`wait_for_shutdown` does without blocking the loop.
"""

from __future__ import annotations

import asyncio
import threading


def new_shutdown_event() -> threading.Event:
    """Return the shutdown flag for a wizard's settings object.

    Thread-safe to ``set()`` from anywhere: a route handler on the server
    loop, the pywebview main thread, or the CLI's error path.
    """
    return threading.Event()


async def wait_for_shutdown(event: threading.Event) -> None:
    """Await a :class:`threading.Event` without blocking the event loop.

    A bridge thread does the blocking wait and hands the result back via
    ``call_soon_threadsafe``, so the caller stays cancellable and the loop
    keeps serving requests while waiting.

    The bridge is a daemon thread on purpose. ``asyncio.to_thread`` would
    read more neatly, but it runs on the default executor, whose threads
    are non-daemon and joined by an ``atexit`` hook — a wizard that exits
    without the event ever being set (Ctrl-C, a crash in the GUI) would
    hang at interpreter shutdown waiting for a thread that never returns.
    """
    if event.is_set():
        return
    loop = asyncio.get_running_loop()
    reached = asyncio.Event()

    def _bridge() -> None:
        event.wait()
        loop.call_soon_threadsafe(reached.set)

    threading.Thread(target=_bridge, name="regstack-wizard-shutdown", daemon=True).start()
    await reached.wait()


__all__ = ["new_shutdown_event", "wait_for_shutdown"]
