from __future__ import annotations

import re
from datetime import timedelta

import pytest

from regstack.email.console import ConsoleEmailService

REGISTER = "/api/auth/register"
VERIFY = "/api/auth/verify"
RESEND = "/api/auth/resend-verification"
LOGIN = "/api/auth/login"

CREDS = {
    "email": "alice@example.com",
    "password": "hunter2hunter2",
    "full_name": "Alice",
}


def _extract_token(url: str) -> str:
    match = re.search(r"token=([^&\s]+)", url)
    assert match, f"no token in url: {url!r}"
    return match.group(1)


@pytest.mark.asyncio
async def test_register_creates_pending_when_verification_required(make_client) -> None:
    async with make_client(require_verification=True) as (rs, client):
        r = await client.post(REGISTER, json=CREDS)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending_verification"
        assert body["email"] == CREDS["email"]

        assert await rs.users.get_by_email(CREDS["email"]) is None
        pending = await rs.pending.find_by_email(CREDS["email"])
        assert pending is not None

        # The console outbox should hold a verification email.
        assert isinstance(rs.email, ConsoleEmailService)
        assert len(rs.email.outbox) == 1
        message = rs.email.outbox[0]
        assert message.to == CREDS["email"]
        assert "verify" in message.text.lower() or "confirm" in message.text.lower()


@pytest.mark.asyncio
async def test_verification_then_login(make_client) -> None:
    async with make_client(require_verification=True) as (rs, client):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        token = _extract_token(rs.email.outbox[0].text)

        r = await client.post(VERIFY, json={"token": token})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == CREDS["email"]
        assert body["is_verified"] is True

        # Pending row gone, real user present.
        assert await rs.pending.find_by_email(CREDS["email"]) is None
        user = await rs.users.get_by_email(CREDS["email"])
        assert user is not None and user.is_verified

        # Login succeeds.
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_unverified_user_cannot_login(make_client) -> None:
    async with make_client(require_verification=True) as (_, client):
        await client.post(REGISTER, json=CREDS)
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        # No real user exists yet — unknown email path returns 401, not 403.
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_verification_token_expires(make_client, frozen_clock) -> None:
    async with make_client(require_verification=True, verification_token_ttl_seconds=60) as (
        rs,
        client,
    ):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        token = _extract_token(rs.email.outbox[0].text)

        frozen_clock.advance(timedelta(seconds=120))
        r = await client.post(VERIFY, json={"token": token})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_invalid_verification_token(make_client) -> None:
    async with make_client(require_verification=True) as (_, client):
        r = await client.post(VERIFY, json={"token": "totally-bogus-token"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_resend_invalidates_old_token(make_client) -> None:
    async with make_client(require_verification=True) as (rs, client):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        old_token = _extract_token(rs.email.outbox[0].text)

        r = await client.post(RESEND, json={"email": CREDS["email"]})
        assert r.status_code == 202
        assert len(rs.email.outbox) == 2
        new_token = _extract_token(rs.email.outbox[1].text)
        assert new_token != old_token

        # Old token should fail; new token should succeed.
        r = await client.post(VERIFY, json={"token": old_token})
        assert r.status_code == 400
        r = await client.post(VERIFY, json={"token": new_token})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_resend_silent_for_unknown_email(make_client) -> None:
    async with make_client(require_verification=True) as (rs, client):
        r = await client.post(RESEND, json={"email": "nobody@example.com"})
        assert r.status_code == 202
        assert isinstance(rs.email, ConsoleEmailService)
        assert rs.email.outbox == []  # nothing sent


@pytest.mark.asyncio
async def test_resend_silent_for_already_real_user(make_client) -> None:
    async with make_client(require_verification=True) as (rs, client):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        token = _extract_token(rs.email.outbox[0].text)
        await client.post(VERIFY, json={"token": token})
        rs.email.outbox.clear()

        r = await client.post(RESEND, json={"email": CREDS["email"]})
        assert r.status_code == 202
        assert rs.email.outbox == []
