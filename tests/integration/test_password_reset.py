from __future__ import annotations

import re
from datetime import timedelta

import pytest

from regstack.email.console import ConsoleEmailService

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"
FORGOT = "/api/auth/forgot-password"
RESET = "/api/auth/reset-password"

CREDS = {
    "email": "alice@example.com",
    "password": "hunter2hunter2",
    "full_name": "Alice",
}
NEW_PASSWORD = "newhunter3newhunter3"


def _extract_token(url: str) -> str:
    match = re.search(r"token=([^&\s]+)", url)
    assert match, f"no token in url: {url!r}"
    return match.group(1)


@pytest.mark.asyncio
async def test_forgot_then_reset_then_login_with_new_password(make_client) -> None:
    async with make_client() as (rs, client):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()

        r = await client.post(FORGOT, json={"email": CREDS["email"]})
        assert r.status_code == 202
        assert len(rs.email.outbox) == 1
        token = _extract_token(rs.email.outbox[0].text)

        r = await client.post(RESET, json={"token": token, "new_password": NEW_PASSWORD})
        assert r.status_code == 200, r.text

        # Old password now rejected, new password works.
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        assert r.status_code == 401
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": NEW_PASSWORD})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_forgot_returns_202_for_unknown_email(make_client) -> None:
    async with make_client() as (rs, client):
        r = await client.post(FORGOT, json={"email": "nobody@example.com"})
        assert r.status_code == 202
        assert isinstance(rs.email, ConsoleEmailService)
        assert rs.email.outbox == []


@pytest.mark.asyncio
async def test_reset_bulk_revokes_existing_sessions(make_client) -> None:
    async with make_client() as (rs, client):
        await client.post(REGISTER, json=CREDS)
        # Establish a live session.
        login = await client.post(
            LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]}
        )
        token = login.json()["access_token"]
        r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 200

        # Trigger reset.
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()
        await client.post(FORGOT, json={"email": CREDS["email"]})
        reset_token = _extract_token(rs.email.outbox[0].text)
        await client.post(RESET, json={"token": reset_token, "new_password": NEW_PASSWORD})

        # Original session token must be rejected — bulk revoke fired.
        r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_reset_token_purpose_separation(make_client) -> None:
    """A regular login JWT must not satisfy the reset endpoint, even though
    both are issued by the same JwtCodec — the per-purpose derived secret
    means different keys signed them.
    """
    async with make_client() as (_, client):
        await client.post(REGISTER, json=CREDS)
        login = await client.post(
            LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]}
        )
        session_token = login.json()["access_token"]

        r = await client.post(RESET, json={"token": session_token, "new_password": NEW_PASSWORD})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_expired_reset_token(make_client, frozen_clock) -> None:
    async with make_client(password_reset_token_ttl_seconds=120) as (rs, client):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()
        await client.post(FORGOT, json={"email": CREDS["email"]})
        token = _extract_token(rs.email.outbox[0].text)

        frozen_clock.advance(timedelta(seconds=180))
        r = await client.post(RESET, json={"token": token, "new_password": NEW_PASSWORD})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_reset_disabled_returns_404(make_client) -> None:
    async with make_client(enable_password_reset=False) as (_, client):
        # The router is conditionally mounted, so the path doesn't exist at all.
        r = await client.post(FORGOT, json={"email": CREDS["email"]})
        assert r.status_code == 404
