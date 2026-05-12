"""Per-route IP rate limit tests.

These exercise the slowapi integration end-to-end: a configured
``*_rate_limit`` string causes regstack to decorate the matching route
with ``limiter.limit(...)``, and the slowapi default handler returns
HTTP 429 once the bucket is empty.

The lockout suite already verifies that per-account credential
hammering returns 429 *before* password verification; this module is
specifically about per-IP throttling at the FastAPI layer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from regstack import RegStack, RegStackConfig
from regstack.auth.clock import FrozenClock
from regstack.config.schema import EmailConfig
from regstack.email.console import ConsoleEmailService

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
FORGOT = "/api/auth/forgot-password"
RESEND = "/api/auth/resend-verification"


def _wire_slowapi(app: FastAPI, limiter: Limiter) -> None:
    """Register the slowapi exception handler + state required to serve 429s."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@pytest_asyncio.fixture
async def rate_limit_factory(database_url, jwt_secret, _ensure_postgres_db):
    """Build an isolated (regstack, client, limiter) trio per test.

    Mirrors the conftest ``make_client`` factory but also adds slowapi
    wiring at the app level so per-route limits actually return 429.
    Takes ``_ensure_postgres_db`` so the postgres parametrization can
    issue CREATE/DROP DATABASE before/after the test.
    """

    rs_holder: list[RegStack] = []

    @asynccontextmanager
    async def _factory(
        *, limiter: Limiter | None = None, auto_limiter: bool = False, **cfg_overrides
    ) -> AsyncIterator[tuple[RegStack, AsyncClient, Limiter | None]]:
        url, mongo_db = database_url
        base: dict[str, object] = dict(
            jwt_secret=jwt_secret,
            database_url=url,
            require_verification=False,
            allow_registration=True,
            rate_limit_disabled=True,
            email=EmailConfig(backend="console", from_address="test@example.com"),
        )
        if mongo_db is not None:
            base["mongodb_database"] = mongo_db
        base.update(cfg_overrides)
        cfg = RegStackConfig.load(toml_path="/dev/null", secrets_env_path="/dev/null", **base)

        if limiter is None and not auto_limiter:
            limiter = Limiter(key_func=get_remote_address)

        rs = RegStack(
            config=cfg,
            clock=FrozenClock(),
            email_service=ConsoleEmailService(),
            rate_limiter=limiter,  # None when auto_limiter=True
        )
        rs_holder.append(rs)
        await rs.install_schema()

        app = FastAPI()
        active_limiter = limiter if limiter is not None else rs.rate_limiter
        # active_limiter can still be None when no *_rate_limit fields are set —
        # in that branch the router never asks for a Limiter and the test is
        # exercising the "no rate limits" path.
        if active_limiter is not None:
            _wire_slowapi(app, active_limiter)
        app.include_router(rs.router, prefix="/api/auth")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield rs, ac, active_limiter

    try:
        yield _factory
    finally:
        for rs in rs_holder:
            await rs.aclose()


@pytest.mark.asyncio
async def test_no_rate_limit_when_unset(rate_limit_factory) -> None:
    """With no ``*_rate_limit`` fields set, requests are never throttled."""
    async with rate_limit_factory() as (_, client, _):
        # Hammer /forgot-password — without a limit, every request is 202.
        for _ in range(20):
            resp = await client.post(FORGOT, json={"email": "nobody@example.com"})
            assert resp.status_code == 202


@pytest.mark.asyncio
async def test_login_route_limited(rate_limit_factory) -> None:
    """``login_rate_limit`` caps requests-per-IP regardless of credentials."""
    async with rate_limit_factory(login_rate_limit="2/minute") as (_, client, _):
        # First two attempts: 401 (no such user), under the limit.
        for _ in range(2):
            resp = await client.post(
                LOGIN, json={"email": "no-such@example.com", "password": "x" * 12}
            )
            assert resp.status_code == 401, resp.text

        # Third attempt within the minute: 429.
        resp = await client.post(LOGIN, json={"email": "no-such@example.com", "password": "x" * 12})
        assert resp.status_code == 429
        # Retry-After is opt-in (slowapi sets it only with headers_enabled=True);
        # status code alone is the canonical signal here.


@pytest.mark.asyncio
async def test_forgot_password_route_limited(rate_limit_factory) -> None:
    """``forgot_password_rate_limit`` throttles independently of /login."""
    async with rate_limit_factory(forgot_password_rate_limit="1/minute") as (_, client, _):
        first = await client.post(FORGOT, json={"email": "a@example.com"})
        assert first.status_code == 202
        second = await client.post(FORGOT, json={"email": "b@example.com"})
        assert second.status_code == 429


@pytest.mark.asyncio
async def test_limits_are_per_route(rate_limit_factory) -> None:
    """Hitting the /login limit does not consume the /forgot-password bucket."""
    async with rate_limit_factory(
        login_rate_limit="1/minute",
        forgot_password_rate_limit="5/minute",
    ) as (_, client, _):
        # Burn the /login bucket.
        for _ in range(2):
            await client.post(LOGIN, json={"email": "x@example.com", "password": "y" * 12})

        # /forgot-password still has budget.
        resp = await client.post(FORGOT, json={"email": "x@example.com"})
        assert resp.status_code == 202


@pytest.mark.asyncio
async def test_missing_limiter_with_limits_configured_raises(
    database_url, jwt_secret, _ensure_postgres_db
) -> None:
    """If a limit string is configured but no Limiter is available, accessing
    ``rs.router`` raises RuntimeError. Failing closed is the safer default —
    we never want to silently disable a configured protection.
    """
    url, mongo_db = database_url
    overrides: dict[str, object] = dict(
        jwt_secret=jwt_secret,
        database_url=url,
        require_verification=False,
        allow_registration=True,
        rate_limit_disabled=True,
        login_rate_limit="5/minute",
        email=EmailConfig(backend="console", from_address="test@example.com"),
    )
    if mongo_db is not None:
        overrides["mongodb_database"] = mongo_db
    cfg = RegStackConfig.load(toml_path="/dev/null", secrets_env_path="/dev/null", **overrides)

    rs = RegStack(
        config=cfg,
        clock=FrozenClock(),
        email_service=ConsoleEmailService(),
        rate_limiter=None,  # no host limiter
    )
    try:
        await rs.install_schema()
        # Pretend slowapi isn't installed by stubbing the import.
        import sys

        saved = sys.modules.get("slowapi")
        sys.modules["slowapi"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="rate limits are configured but slowapi"):
                _ = rs.router
        finally:
            if saved is not None:
                sys.modules["slowapi"] = saved
            else:
                sys.modules.pop("slowapi", None)
    finally:
        await rs.aclose()


@pytest.mark.asyncio
async def test_host_supplied_limiter_is_reused(rate_limit_factory) -> None:
    """When the host passes its own Limiter, regstack does not build a new one."""
    host_limiter = Limiter(key_func=get_remote_address)
    async with rate_limit_factory(
        limiter=host_limiter,
        login_rate_limit="1/minute",
    ) as (rs, _, active):
        assert rs.rate_limiter is host_limiter
        assert active is host_limiter
