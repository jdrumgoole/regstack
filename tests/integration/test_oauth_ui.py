"""SSR-page tests for the OAuth M4 polish.

Mostly smoke tests — the page bodies are static, so we just verify
the right things are rendered when OAuth is enabled vs off, plus
that ``/oauth/providers`` returns the expected shape for an
authenticated user with linked + unlinked providers.

Parametrized over all three backends so the linked-providers query
runs against each.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from regstack import RegStack
from regstack.config.schema import OAuthConfig
from regstack.email.console import ConsoleEmailService
from regstack.models.oauth_identity import OAuthIdentity
from regstack.models.user import BaseUser
from tests._fake_google import FakeGoogleProvider


@asynccontextmanager
async def _build_app(
    config,
    backend_kind,
    jwt_secret,
    database_url,
    frozen_clock,
    *,
    enable_oauth: bool,
):
    from tests.conftest import _build_config

    url, mongo_db = database_url
    overrides: dict[str, object] = {
        "enable_ui_router": True,
        "enable_oauth": enable_oauth,
    }
    if enable_oauth:
        overrides["oauth"] = OAuthConfig(
            google_client_id="fake-google-id",
            google_client_secret="fake-secret",
        )
    cfg = _build_config(
        jwt_secret=jwt_secret,
        database_url=url,
        mongo_db_name=mongo_db,
        **overrides,
    )
    rs = RegStack(config=cfg, clock=frozen_clock, email_service=ConsoleEmailService())
    if enable_oauth:
        rs.oauth.register(FakeGoogleProvider(client_id=cfg.oauth.google_client_id))
    await rs.install_schema()

    app = FastAPI()
    app.include_router(rs.router, prefix="/api/auth")
    app.include_router(rs.ui_router, prefix="/account")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield rs, client
    finally:
        await rs.aclose()


@pytest_asyncio.fixture
async def oauth_ui(config, backend_kind, jwt_secret, database_url, frozen_clock):
    async with _build_app(
        config, backend_kind, jwt_secret, database_url, frozen_clock, enable_oauth=True
    ) as ctx:
        yield ctx


@pytest_asyncio.fixture
async def no_oauth_ui(config, backend_kind, jwt_secret, database_url, frozen_clock):
    async with _build_app(
        config,
        backend_kind,
        jwt_secret,
        database_url,
        frozen_clock,
        enable_oauth=False,
    ) as ctx:
        yield ctx


@pytest.mark.asyncio
async def test_login_page_shows_oauth_button_when_enabled(oauth_ui) -> None:
    _, client = oauth_ui
    r = await client.get("/account/login")
    assert r.status_code == 200
    body = r.text
    assert "Sign in with Google" in body
    assert 'data-rs-oauth-link="google"' in body


@pytest.mark.asyncio
async def test_login_page_omits_oauth_when_disabled(no_oauth_ui) -> None:
    _, client = no_oauth_ui
    r = await client.get("/account/login")
    assert r.status_code == 200
    assert "Sign in with Google" not in r.text


@pytest.mark.asyncio
async def test_oauth_complete_page_renders(oauth_ui) -> None:
    _, client = oauth_ui
    r = await client.get("/account/oauth-complete?id=anything")
    assert r.status_code == 200
    assert "Signing you in" in r.text


@pytest.mark.asyncio
async def test_me_page_shows_connected_accounts_section(oauth_ui) -> None:
    _, client = oauth_ui
    r = await client.get("/account/me")
    assert r.status_code == 200
    assert "Connected accounts" in r.text
    assert "data-rs-oauth-section" in r.text


@pytest.mark.asyncio
async def test_providers_endpoint_lists_linked_and_available(oauth_ui) -> None:
    rs, client = oauth_ui

    user = BaseUser(
        email="alice@example.com",
        hashed_password=rs.password_hasher.hash("hunter2hunter2"),
        is_active=True,
        is_verified=True,
    )
    user = await rs.users.create(user)
    assert user.id is not None
    await rs.oauth_identities.create(
        OAuthIdentity(
            user_id=user.id,
            provider="google",
            subject_id="g-ui-001",
            email="alice@example.com",
        )
    )
    token, _ = rs.jwt.encode(user.id)

    r = await client.get(
        "/api/auth/oauth/providers",
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] == ["google"]
    assert len(body["linked"]) == 1
    assert body["linked"][0]["provider"] == "google"
    assert body["linked"][0]["email"] == "alice@example.com"
    assert body["linked"][0]["linked_at"]


@pytest.mark.asyncio
async def test_providers_endpoint_requires_authentication(oauth_ui) -> None:
    _, client = oauth_ui
    r = await client.get("/api/auth/oauth/providers")
    assert r.status_code == 401
