"""Regression tests for wizard shutdown signalling across threads.

`regstack oauth setup` crashed on startup with

    RuntimeError: <asyncio.locks.Event ...> is bound to a different event loop

because `shutdown_event` was an `asyncio.Event`, and a wizard runs three
threads at once: pywebview owns the main thread (a macOS requirement),
uvicorn runs its loop in a background thread, and a watcher thread waits
for shutdown to destroy the window. An `asyncio.Event` binds to the first
loop that touches it, so the watcher's `asyncio.run()` created a second
loop and blew up on the Event uvicorn's loop already owned.

Nothing caught it because `window.py` is excluded from coverage — there's
no headless path through pywebview. These tests reach the same code the
crash was in without opening a window: the defect is in the *threading
arrangement*, and that is reproducible headlessly.

All three wizards share a duplicated scaffold, so every test runs against
all three. That duplication is why one bug became three.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import pathlib
import threading
import time
from typing import Any

import pytest

WIZARDS = ["oauth_google", "theme_designer", "ses"]
_WIZARD_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "regstack" / "wizard"


def _make_server(wizard: str, tmp_path: pathlib.Path) -> Any:
    """Build (but don't start) a wizard server descriptor."""
    mod = importlib.import_module(f"regstack.wizard.{wizard}.server")
    factory = next(
        getattr(mod, name)
        for name in ("make_wizard_server", "make_designer_server", "make_server")
        if hasattr(mod, name)
    )
    return factory(target_dir=tmp_path)


# --- The primitive itself ---------------------------------------------------


@pytest.mark.parametrize("wizard", WIZARDS)
def test_shutdown_event_is_thread_safe_not_loop_bound(wizard: str, tmp_path: pathlib.Path) -> None:
    """`shutdown_event` must be a `threading.Event`.

    An `asyncio.Event` cannot be waited on or set from a thread other than
    the one owning its loop, which is precisely what the window watcher and
    the CLI teardown both do.
    """
    event = _make_server(wizard, tmp_path).settings.shutdown_event
    assert isinstance(event, threading.Event), (
        f"{wizard}.settings.shutdown_event is {type(event)!r}; an asyncio.Event "
        "binds to one loop and breaks the window watcher"
    )
    assert not isinstance(event, asyncio.Event)


@pytest.mark.parametrize("wizard", WIZARDS)
def test_window_module_does_not_start_its_own_event_loop(wizard: str) -> None:
    """No `asyncio.run` in a window module.

    The watcher thread must block on the threading primitive directly. A
    second event loop there is the exact shape of the original crash, and
    an AST check catches it even though the module can't be executed
    headlessly.
    """
    source = (_WIZARD_DIR / wizard / "window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        f"line {node.lineno}: asyncio.{node.func.attr}(...)"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
        and node.func.attr in {"run", "new_event_loop", "get_event_loop"}
    ]
    assert not offenders, (
        f"{wizard}/window.py starts an event loop in the watcher thread: {offenders}"
    )


# --- The arrangement that actually crashed ---------------------------------


@pytest.mark.parametrize("wizard", WIZARDS)
def test_event_is_waitable_from_a_foreign_thread_while_a_loop_owns_it(
    wizard: str, tmp_path: pathlib.Path
) -> None:
    """The crash, reduced.

    A loop in one thread awaits the event; a second thread then waits on
    the same event with no loop of its own. Under `asyncio.Event` the
    second wait raised RuntimeError. It must now simply block and return.
    """
    from regstack.wizard._shutdown import wait_for_shutdown

    event = _make_server(wizard, tmp_path).settings.shutdown_event
    loop_ready = threading.Event()
    loop_returned = threading.Event()

    def _owning_loop() -> None:
        async def _go() -> None:
            loop_ready.set()
            await wait_for_shutdown(event)

        asyncio.run(_go())
        loop_returned.set()

    threading.Thread(target=_owning_loop, daemon=True).start()
    assert loop_ready.wait(timeout=5), "server loop never started"
    time.sleep(0.2)  # let it reach the await, binding anything bindable

    errors: list[BaseException] = []
    watcher_returned = threading.Event()

    def _watcher() -> None:
        try:
            event.wait()
        except BaseException as exc:
            errors.append(exc)
        finally:
            watcher_returned.set()

    threading.Thread(target=_watcher, daemon=True).start()
    time.sleep(0.2)
    assert not errors, f"watcher raised before shutdown: {errors}"
    assert not watcher_returned.is_set(), "watcher returned before the event was set"

    # Set from this (third) thread — the pywebview main thread's role.
    event.set()
    assert watcher_returned.wait(timeout=5), "watcher never woke after set()"
    assert not errors, f"watcher raised: {errors}"
    assert loop_returned.wait(timeout=5), "the awaiting loop never woke after set()"


@pytest.mark.parametrize("wizard", WIZARDS)
def test_serve_stops_when_the_event_is_set_from_another_thread(
    wizard: str, tmp_path: pathlib.Path
) -> None:
    """End to end, minus the window: a real uvicorn on a real port stops
    when the event is set from the main thread.

    This is what "close the wizard window and the process exits" depends
    on. The server picks its own free port, so this stays parallel-safe.
    """
    mod = importlib.import_module(f"regstack.wizard.{wizard}.server")
    server = _make_server(wizard, tmp_path)
    finished = threading.Event()

    def _serve_forever() -> None:
        try:
            asyncio.run(mod.serve(server))
        finally:
            finished.set()

    thread = threading.Thread(target=_serve_forever, daemon=True)
    thread.start()

    # Wait for the port to accept connections before signalling, so we're
    # testing shutdown of a live server rather than a race with startup.
    import socket

    deadline = time.monotonic() + 10
    up = False
    while time.monotonic() < deadline and not up:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            up = sock.connect_ex(("127.0.0.1", server.port)) == 0
        if not up:
            time.sleep(0.1)
    assert up, f"{wizard} server never came up on :{server.port}"

    server.settings.shutdown_event.set()
    assert finished.wait(timeout=10), (
        f"{wizard} serve() did not return after shutdown_event was set from "
        "another thread — closing the window would leave the process running"
    )
