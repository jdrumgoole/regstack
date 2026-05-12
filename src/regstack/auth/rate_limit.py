"""Optional per-route IP rate limiting via slowapi.

This wraps slowapi so the rest of regstack imports cleanly even when the
``rate_limit`` extra is not installed. Hosts opt in by passing a
``Limiter`` to ``RegStack(rate_limiter=...)`` or by installing
``regstack[rate_limit]`` and letting regstack build one from
``RegStackConfig``.

Per-route limits are orthogonal to the existing per-account login
lockout (``LockoutService``):

- **Lockout** counts failures *per email* in a sliding window and
  returns 429 *before* password verification — it defends one account
  against credential-stuffing.
- **Rate limits** count requests *per source IP* across all accounts
  and apply to every endpoint listed in
  ``RegStackConfig.*_rate_limit`` — they defend against a single IP
  spamming an endpoint (registration, password-reset, verify).

Both can coexist; a defender who only configures lockout is still
vulnerable to a botnet that spams ``/forgot-password`` from many IPs.
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any

from fastapi import Request
from fastapi.routing import APIRoute

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import APIRouter

    from regstack.config.schema import RegStackConfig


# Map a config field name → the router path it limits. Each path is
# resolved relative to the auth router's mount prefix; rate-limit
# decoration walks the assembled APIRouter and matches by `route.path`.
ROUTE_LIMIT_MAP: dict[str, str] = {
    "login_rate_limit": "/login",
    "register_rate_limit": "/register",
    "forgot_password_rate_limit": "/forgot-password",
    "reset_password_rate_limit": "/reset-password",
    "verify_rate_limit": "/verify",
    "resend_verification_rate_limit": "/resend-verification",
    "change_password_rate_limit": "/change-password",
    "change_email_rate_limit": "/change-email",
    "confirm_email_change_rate_limit": "/confirm-email-change",
    "delete_account_rate_limit": "/account",
}


def build_default_limiter() -> Any:
    """Construct a sensible in-memory ``slowapi.Limiter`` for hosts that
    don't already use slowapi.

    Raises ``ImportError`` if the ``rate_limit`` extra isn't installed.
    """
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    return Limiter(key_func=get_remote_address)


def collect_route_limits(config: RegStackConfig) -> dict[str, str]:
    """Return ``{path: limit_string}`` for each route the host has
    configured a limit on. Returns an empty dict when nothing is set;
    the caller should skip decoration entirely in that case.
    """
    out: dict[str, str] = {}
    for field, path in ROUTE_LIMIT_MAP.items():
        value = getattr(config, field, None)
        if value:
            out[path] = value
    return out


def _ensure_request_parameter(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return a wrapper that declares ``request: Request`` in its signature
    if ``fn`` doesn't already have one.

    slowapi's ``Limiter.limit`` looks for a ``Request`` parameter on the
    decorated callable so it can read the client IP. regstack's endpoint
    handlers usually take just a JSON payload (and FastAPI dependencies
    via ``Depends``), so without a wrapper slowapi raises ``Exception:
    No "request" or "websocket" argument``. The wrapper preserves the
    original parameters and adds a leading ``request: Request`` that
    FastAPI resolves automatically and the inner function ignores.
    """
    sig = inspect.signature(fn)
    if "request" in sig.parameters:
        return fn

    @functools.wraps(fn)
    async def _wrapper(request: Request, *args: Any, **kwargs: Any) -> Any:
        return await fn(*args, **kwargs)

    new_params = [
        inspect.Parameter(
            "request",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=Request,
        ),
        *sig.parameters.values(),
    ]
    _wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
    return _wrapper


def apply_route_limits(
    router: APIRouter,
    limiter: Any,
    path_to_limit: dict[str, str],
) -> None:
    """Decorate each route in ``router`` whose path appears in
    ``path_to_limit`` with ``limiter.limit(rate_string)``.

    Walks the assembled router rather than annotating endpoint
    callables at definition time so the routers stay slowapi-agnostic
    on a base install.

    Args:
        router: The composite APIRouter returned by ``build_router``.
        limiter: A ``slowapi.Limiter`` instance.
        path_to_limit: ``{"/login": "30/minute;200/hour", ...}``.
    """
    for route in router.routes:
        # Mount points / WebSocketRoutes / etc. aren't decorate-able. Only
        # APIRoutes carry a path + endpoint we can rewrap.
        if not isinstance(route, APIRoute):
            continue
        rate = path_to_limit.get(route.path)
        if not rate:
            continue
        wrapped = _ensure_request_parameter(route.endpoint)
        route.endpoint = limiter.limit(rate)(wrapped)
