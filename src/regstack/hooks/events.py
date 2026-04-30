from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("regstack.hooks")

Handler = Callable[..., Awaitable[None] | None]

# Known event names. Hosts may also subscribe to custom events at their own risk.
KNOWN_EVENTS = {
    "user_registered",
    "user_logged_in",
    "user_logged_out",
    "user_verified",
    "password_reset_requested",
    "password_reset_completed",
    "password_changed",
    "user_deleted",
    "oauth_signin_started",
    "oauth_signin_completed",
    "oauth_account_linked",
    "oauth_account_unlinked",
}


class HookRegistry:
    """Tiny in-process event bus for auth-flow side-effects.

    Hosts subscribe handlers (sync or async) to event names; regstack
    fires events at the natural points in each flow (registration,
    login, password change, etc.). Handlers run concurrently.

    Exceptions raised by a handler are **logged and swallowed** so a
    misbehaving notification handler cannot break the primary auth
    flow. If you need a hard dependency on a side-effect succeeding,
    do that work synchronously in a wrapper around the regstack call —
    not in a hook.

    See :data:`KNOWN_EVENTS` for the events regstack itself fires;
    hosts may subscribe to custom event names too.
    """

    def __init__(self) -> None:
        """Construct an empty registry."""
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> None:
        """Subscribe a handler to an event.

        Multiple handlers per event are allowed and are fired
        concurrently. Handlers can be sync or async; an async handler
        is awaited (in parallel with its siblings) inside :meth:`fire`.

        Args:
            event: The event name (e.g. ``"user_registered"``). Not
                validated against :data:`KNOWN_EVENTS` — hosts can use
                custom names.
            handler: A callable invoked with the event's keyword
                arguments. Returning a value is fine; it's ignored.
        """
        self._handlers[event].append(handler)

    async def fire(self, event: str, /, **kwargs: Any) -> None:
        """Run every handler subscribed to ``event``.

        Sync handlers run inline (in registration order). Async
        handlers are awaited concurrently via ``asyncio.gather``.
        Exceptions in either kind are logged and discarded — they
        never propagate.

        Args:
            event: The event name to dispatch.
            **kwargs: Keyword arguments forwarded to every handler.
                regstack passes a contextually-relevant set per event
                (e.g. ``user`` for ``"user_registered"``).
        """
        handlers = self._handlers.get(event, ())
        if not handlers:
            return
        coros = []
        for handler in handlers:
            try:
                result = handler(**kwargs)
            except Exception:
                log.exception("regstack hook %r raised synchronously", event)
                continue
            if inspect.isawaitable(result):
                coros.append(_swallow(event, result))
        if coros:
            await asyncio.gather(*coros)


async def _swallow(event: str, awaitable: Awaitable[None]) -> None:
    try:
        await awaitable
    except Exception:
        log.exception("regstack hook %r raised in awaitable", event)
