"""End-to-end OAuth tests for the Google router.

The 16 cases from ``tasks/oauth-design.md``, parametrized over all
three backends. The real :class:`GoogleProvider` is replaced with
:class:`tests._fake_google.FakeGoogleProvider` so the suite stays
offline. Everything else — the state row lifecycle, the
identity-resolution policy, the bulk-revoke path through
``rs.users.update_password`` — runs through real production code.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from regstack import RegStack
from regstack.config.schema import OAuthConfig
from regstack.email.console import ConsoleEmailService
from regstack.models.oauth_identity import OAuthIdentity
from regstack.models.user import BaseUser
from regstack.oauth.base import OAuthUserInfo
from tests._fake_google import FakeGoogleProvider

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"
OAUTH_START = "/api/auth/oauth/google/start"
OAUTH_CALLBACK = "/api/auth/oauth/google/callback"
OAUTH_EXCHANGE = "/api/auth/oauth/exchange"
OAUTH_LINK_START = "/api/auth/oauth/google/link/start"
OAUTH_LINK_DELETE = "/api/auth/oauth/google/link"


def _info(
    *,
    subject_id: str,
    email: str | None = "alice@example.com",
    email_verified: bool = True,
    full_name: str | None = "Alice Example",
) -> OAuthUserInfo:
    return OAuthUserInfo(
        subject_id=subject_id,
        email=email,
        email_verified=email_verified,
        full_name=full_name,
        picture_url=None,
    )


@asynccontextmanager
async def _oauth_app(
    config,
    backend_kind,
    jwt_secret,
    database_url,
    frozen_clock,
    *,
    auto_link_verified_emails: bool = False,
    enforce_mfa_on_oauth_signin: bool = False,
):
    """Build a RegStack + FastAPI app with a FakeGoogleProvider registered.

    Mirrors ``tests/conftest.py:_factory`` but registers the fake
    provider after construction and before the router is first built.
    Yields ``(rs, fake, client)``.
    """
    from tests.conftest import _build_config

    url, mongo_db = database_url
    cfg = _build_config(
        jwt_secret=jwt_secret,
        database_url=url,
        mongo_db_name=mongo_db,
        enable_oauth=True,
        oauth=OAuthConfig(
            google_client_id="fake-google-client-id",
            google_client_secret="fake-secret",
            auto_link_verified_emails=auto_link_verified_emails,
            enforce_mfa_on_oauth_signin=enforce_mfa_on_oauth_signin,
        ),
    )
    rs = RegStack(config=cfg, clock=frozen_clock, email_service=ConsoleEmailService())
    fake = FakeGoogleProvider(client_id=cfg.oauth.google_client_id)
    rs.oauth.register(fake)
    await rs.install_schema()

    app = FastAPI()
    app.include_router(rs.router, prefix="/api/auth")
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield rs, fake, client
    finally:
        await rs.aclose()


@pytest_asyncio.fixture
async def oauth_client(
    config,
    backend_kind,
    jwt_secret,
    database_url,
    frozen_clock,
):
    """Default-config OAuth fixture: auto-link off, MFA off."""
    async with _oauth_app(config, backend_kind, jwt_secret, database_url, frozen_clock) as ctx:
        yield ctx


def _state_id_from_redirect(resp) -> str:
    """Extract the ``state`` query parameter from a 302 redirect."""
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    qs = parse_qs(urlsplit(location).query)
    return qs["state"][0]


async def _start_signin(client) -> tuple[str, str]:
    """Hit /oauth/google/start; return (state_id, fake_authorization_url)."""
    r = await client.get(OAUTH_START, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    state = _state_id_from_redirect(r)
    return state, loc


async def _callback(client, *, state: str, code: str = "google-code") -> Any:
    return await client.get(
        OAUTH_CALLBACK,
        params={"state": state, "code": code},
        follow_redirects=False,
    )


async def _exchange(client, state_id: str):
    return await client.post(OAUTH_EXCHANGE, json={"id": state_id})


# ---------------------------------------------------------------------------
# 1 — new signup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signup_creates_user_and_identity(oauth_client) -> None:
    rs, fake, client = oauth_client
    fake.queue_user(subject_id="g-001", email="newcomer@example.com")

    state_id, _ = await _start_signin(client)
    callback = await _callback(client, state=state_id)
    assert callback.status_code == 302
    assert callback.headers["location"].startswith("/account/oauth-complete?id=")

    exchange = await _exchange(client, state_id)
    assert exchange.status_code == 200, exchange.text
    body = exchange.json()
    assert body["access_token"]
    assert body["redirect_to"] == "/account/me"

    user = await rs.users.get_by_email("newcomer@example.com")
    assert user is not None
    assert user.is_verified is True
    assert user.hashed_password is None  # OAuth-only user
    identities = await rs.oauth_identities.list_for_user(user.id)
    assert [i.provider for i in identities] == ["google"]
    assert identities[0].subject_id == "g-001"


# ---------------------------------------------------------------------------
# 2 — repeat sign-in (existing identity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeat_signin_uses_existing_identity(oauth_client) -> None:
    rs, fake, client = oauth_client

    # First sign-in creates the account.
    fake.queue_user(subject_id="g-002", email="repeat@example.com")
    s1, _ = await _start_signin(client)
    await _callback(client, state=s1)
    user = await rs.users.get_by_email("repeat@example.com")
    assert user is not None

    # Second sign-in: same subject -> same user.
    fake.queue_user(subject_id="g-002", email="repeat@example.com")
    s2, _ = await _start_signin(client)
    cb2 = await _callback(client, state=s2)
    assert cb2.status_code == 302
    ex2 = await _exchange(client, s2)
    assert ex2.status_code == 200

    # Touch_last_used must have moved past None.
    identity = await rs.oauth_identities.find_by_subject(provider="google", subject_id="g-002")
    assert identity is not None
    assert identity.last_used_at is not None


# ---------------------------------------------------------------------------
# 3 — auto-link blocked by default (existing email-registered user)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signin_blocks_email_match_when_autolink_off(oauth_client) -> None:
    rs, fake, client = oauth_client
    # Create an existing password-registered user.
    existing = BaseUser(
        email="existing@example.com",
        hashed_password=rs.password_hasher.hash("hunter2hunter2"),
        is_active=True,
        is_verified=True,
    )
    await rs.users.create(existing)

    fake.queue_user(subject_id="g-003", email="existing@example.com")
    s, _ = await _start_signin(client)
    cb = await _callback(client, state=s)
    assert cb.status_code == 302
    assert "error=email_in_use" in cb.headers["location"]

    # No identity row should have been inserted.
    user = await rs.users.get_by_email("existing@example.com")
    assert user is not None
    assert await rs.oauth_identities.list_for_user(user.id) == []


# ---------------------------------------------------------------------------
# 4 — auto-link allowed via opt-in flag (with email_verified=true)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signin_autolinks_when_flag_on_and_email_verified(
    config, backend_kind, jwt_secret, database_url, frozen_clock
) -> None:
    async with _oauth_app(
        config,
        backend_kind,
        jwt_secret,
        database_url,
        frozen_clock,
        auto_link_verified_emails=True,
    ) as (rs, fake, client):
        existing = BaseUser(
            email="existing@example.com",
            hashed_password=rs.password_hasher.hash("hunter2hunter2"),
            is_active=True,
            is_verified=True,
        )
        await rs.users.create(existing)

        fake.queue_user(subject_id="g-004", email="existing@example.com", email_verified=True)
        s, _ = await _start_signin(client)
        cb = await _callback(client, state=s)
        assert cb.status_code == 302
        assert "/account/oauth-complete" in cb.headers["location"]

        user = await rs.users.get_by_email("existing@example.com")
        assert user is not None
        identities = await rs.oauth_identities.list_for_user(user.id)
        assert [i.subject_id for i in identities] == ["g-004"]


# ---------------------------------------------------------------------------
# 5 — auto-link refused when email_verified=false even if flag is on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autolink_refused_when_email_unverified(
    config, backend_kind, jwt_secret, database_url, frozen_clock
) -> None:
    async with _oauth_app(
        config,
        backend_kind,
        jwt_secret,
        database_url,
        frozen_clock,
        auto_link_verified_emails=True,
    ) as (rs, fake, client):
        existing = BaseUser(
            email="existing@example.com",
            hashed_password=rs.password_hasher.hash("hunter2hunter2"),
            is_active=True,
            is_verified=True,
        )
        await rs.users.create(existing)
        fake.queue_user(subject_id="g-005", email="existing@example.com", email_verified=False)
        s, _ = await _start_signin(client)
        cb = await _callback(client, state=s)
        assert "error=email_in_use" in cb.headers["location"]


# ---------------------------------------------------------------------------
# 6 — CSRF: bad state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_rejects_unknown_state(oauth_client) -> None:
    _, _, client = oauth_client
    cb = await _callback(client, state="not-a-real-state-id")
    assert cb.status_code == 302
    assert "error=bad_state" in cb.headers["location"]


# ---------------------------------------------------------------------------
# 7 — state expiry (frozen clock advances past TTL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_rejects_expired_state(oauth_client) -> None:
    rs, fake, client = oauth_client
    from datetime import timedelta

    fake.queue_user(subject_id="g-007", email="late@example.com")
    state, _ = await _start_signin(client)

    # Move past TTL.
    rs.clock.advance(timedelta(seconds=rs.config.oauth.state_ttl_seconds + 1))

    cb = await _callback(client, state=state)
    assert "error=state_expired" in cb.headers["location"]


# ---------------------------------------------------------------------------
# 8 — link flow attaches identity to logged-in user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_flow_attaches_to_authenticated_user(oauth_client) -> None:
    rs, fake, client = oauth_client
    user = BaseUser(
        email="alice@example.com",
        hashed_password=rs.password_hasher.hash("hunter2hunter2"),
        is_active=True,
        is_verified=True,
    )
    user = await rs.users.create(user)
    # Sign in.
    r = await client.post(LOGIN, json={"email": "alice@example.com", "password": "hunter2hunter2"})
    token = r.json()["access_token"]
    headers = {"authorization": f"Bearer {token}"}

    r = await client.post(OAUTH_LINK_START, headers=headers)
    assert r.status_code == 200, r.text
    auth_url = r.json()["authorization_url"]
    state = parse_qs(urlsplit(auth_url).query)["state"][0]

    fake.queue_user(subject_id="g-008", email="alice-google@example.com")
    cb = await _callback(client, state=state)
    assert cb.status_code == 302
    assert "/account/oauth-complete" in cb.headers["location"]

    identities = await rs.oauth_identities.list_for_user(user.id)
    assert [i.subject_id for i in identities] == ["g-008"]


# ---------------------------------------------------------------------------
# 9 — refuse to link an identity already linked to another user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_refuses_identity_already_in_use(oauth_client) -> None:
    rs, fake, client = oauth_client

    # User A signs in via OAuth (creates an identity).
    fake.queue_user(subject_id="g-009", email="a@example.com")
    s_a, _ = await _start_signin(client)
    await _callback(client, state=s_a)

    # User B (password-based) tries to link the SAME Google account.
    user_b = BaseUser(
        email="b@example.com",
        hashed_password=rs.password_hasher.hash("hunter2hunter2"),
        is_active=True,
        is_verified=True,
    )
    user_b = await rs.users.create(user_b)
    r = await client.post(LOGIN, json={"email": "b@example.com", "password": "hunter2hunter2"})
    token_b = r.json()["access_token"]
    headers = {"authorization": f"Bearer {token_b}"}

    r = await client.post(OAUTH_LINK_START, headers=headers)
    auth_url = r.json()["authorization_url"]
    state = parse_qs(urlsplit(auth_url).query)["state"][0]

    fake.queue_user(subject_id="g-009", email="a@example.com")  # SAME subject
    cb = await _callback(client, state=state)
    assert "error=identity_in_use" in cb.headers["location"]


# ---------------------------------------------------------------------------
# 10 — re-link the same identity to the same user → 409 already_linked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relink_same_identity_returns_already_linked(oauth_client) -> None:
    rs, fake, client = oauth_client
    fake.queue_user(subject_id="g-010", email="alice@example.com")
    s, _ = await _start_signin(client)
    await _callback(client, state=s)

    user = await rs.users.get_by_email("alice@example.com")
    assert user is not None
    # Mint a session via login isn't possible (no password). Use a JWT directly.
    token, _ = rs.jwt.encode(user.id)
    headers = {"authorization": f"Bearer {token}"}

    r = await client.post(OAUTH_LINK_START, headers=headers)
    assert r.status_code == 200
    auth_url = r.json()["authorization_url"]
    state = parse_qs(urlsplit(auth_url).query)["state"][0]

    fake.queue_user(subject_id="g-010", email="alice@example.com")
    cb = await _callback(client, state=state)
    assert "error=already_linked" in cb.headers["location"]


# ---------------------------------------------------------------------------
# 11 — unlink succeeds for a user with a password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_succeeds_for_password_user(oauth_client) -> None:
    rs, _fake, client = oauth_client

    user = BaseUser(
        email="alice@example.com",
        hashed_password=rs.password_hasher.hash("hunter2hunter2"),
        is_active=True,
        is_verified=True,
    )
    user = await rs.users.create(user)
    await rs.oauth_identities.create(
        OAuthIdentity(
            user_id=user.id,
            provider="google",
            subject_id="g-011",
            email="alice@example.com",
        )
    )
    r = await client.post(LOGIN, json={"email": "alice@example.com", "password": "hunter2hunter2"})
    token = r.json()["access_token"]
    headers = {"authorization": f"Bearer {token}"}

    r = await client.delete(OAUTH_LINK_DELETE, headers=headers)
    assert r.status_code == 200, r.text
    assert await rs.oauth_identities.list_for_user(user.id) == []


# ---------------------------------------------------------------------------
# 12 — refuse to unlink when it's the only sign-in method
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_refused_when_last_auth_method(oauth_client) -> None:
    rs, fake, client = oauth_client
    fake.queue_user(subject_id="g-012", email="oauth-only@example.com")
    state, _ = await _start_signin(client)
    await _callback(client, state=state)

    user = await rs.users.get_by_email("oauth-only@example.com")
    assert user is not None
    assert user.hashed_password is None

    token, _ = rs.jwt.encode(user.id)
    r = await client.delete(
        OAUTH_LINK_DELETE,
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400, r.text
    assert "only sign-in method" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 13 — token-handoff round-trip (single use)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_is_single_use(oauth_client) -> None:
    _rs, fake, client = oauth_client
    fake.queue_user(subject_id="g-013", email="alice@example.com")
    state, _ = await _start_signin(client)
    await _callback(client, state=state)

    first = await _exchange(client, state)
    assert first.status_code == 200
    second = await _exchange(client, state)
    assert second.status_code == 404


# ---------------------------------------------------------------------------
# 14 — bulk-revoke applies to OAuth-issued sessions too
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_session_revoked_by_password_change(oauth_client) -> None:
    rs, fake, client = oauth_client
    # Existing user with a password — auto-link off, so OAuth must NOT
    # auto-link, but for this test we set the linking up directly.
    user = BaseUser(
        email="alice@example.com",
        hashed_password=rs.password_hasher.hash("hunter2hunter2"),
        is_active=True,
        is_verified=True,
    )
    user = await rs.users.create(user)
    await rs.oauth_identities.create(
        OAuthIdentity(
            user_id=user.id,
            provider="google",
            subject_id="g-014",
            email=user.email,
        )
    )

    # Sign in via OAuth.
    fake.queue_user(subject_id="g-014", email="alice@example.com")
    state, _ = await _start_signin(client)
    await _callback(client, state=state)
    body = (await _exchange(client, state)).json()
    oauth_token = body["access_token"]
    headers = {"authorization": f"Bearer {oauth_token}"}

    # Token works.
    r = await client.get(ME, headers=headers)
    assert r.status_code == 200

    # Bulk-revoke via password change (uses the existing path through the
    # repo, no router involved — same effect).
    from datetime import timedelta

    rs.clock.advance(timedelta(seconds=1))
    await rs.users.update_password(user.id, rs.password_hasher.hash("brand-new-pass"))

    # Old OAuth-issued session is now revoked.
    r = await client.get(ME, headers=headers)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 15 — open-redirect protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_rejects_offsite_redirect(oauth_client) -> None:
    _, _, client = oauth_client
    r = await client.get(
        OAUTH_START,
        params={"redirect_to": "https://evil.example/steal"},
        follow_redirects=False,
    )
    assert r.status_code == 400, r.text
    assert "same-origin" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 16 — start endpoint requires a configured provider name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_rejects_unknown_provider(oauth_client) -> None:
    _, _, client = oauth_client
    r = await client.get("/api/auth/oauth/github/start", follow_redirects=False)
    assert r.status_code == 404
