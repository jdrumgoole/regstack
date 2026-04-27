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
}


class HookRegistry:
    """Tiny event bus. Handlers are awaited concurrently; exceptions are logged
    and swallowed so a misbehaving host hook cannot break a primary auth flow.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    async def fire(self, event: str, /, **kwargs: Any) -> None:
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
