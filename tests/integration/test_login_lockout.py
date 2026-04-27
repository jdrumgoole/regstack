from __future__ import annotations

from datetime import timedelta

import pytest

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"

CREDS = {
    "email": "alice@example.com",
    "password": "hunter2hunter2",
    "full_name": "Alice",
}


@pytest.mark.asyncio
async def test_repeated_failures_lock_then_unlock_after_window(make_client, frozen_clock) -> None:
    async with make_client(
        rate_limit_disabled=False,
        login_lockout_threshold=3,
        login_lockout_window_seconds=60,
    ) as (_, client):
        await client.post(REGISTER, json=CREDS)

        bad = {"email": CREDS["email"], "password": "wrong-wrong-wrong"}
        for _ in range(3):
            r = await client.post(LOGIN, json=bad)
            assert r.status_code == 401

        # Now even the correct password is rejected with 429.
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        assert r.status_code == 429
        assert "Retry-After" in r.headers

        # Advance past the window — lockout clears.
        frozen_clock.advance(timedelta(seconds=61))
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_successful_login_clears_failures(make_client) -> None:
    async with make_client(
        rate_limit_disabled=False,
        login_lockout_threshold=3,
        login_lockout_window_seconds=60,
    ) as (_, client):
        await client.post(REGISTER, json=CREDS)

        bad = {"email": CREDS["email"], "password": "wrong-wrong-wrong"}
        for _ in range(2):
            r = await client.post(LOGIN, json=bad)
            assert r.status_code == 401

        good = {"email": CREDS["email"], "password": CREDS["password"]}
        r = await client.post(LOGIN, json=good)
        assert r.status_code == 200

        # Two more bad attempts — should NOT be locked because previous
        # failures were cleared by the successful login.
        for _ in range(2):
            r = await client.post(LOGIN, json=bad)
            assert r.status_code == 401


@pytest.mark.asyncio
async def test_unknown_email_failures_count(make_client) -> None:
    """Failures against an unknown email still count, so an attacker can't
    avoid lockout simply by guessing emails that don't exist.
    """
    async with make_client(
        rate_limit_disabled=False,
        login_lockout_threshold=2,
        login_lockout_window_seconds=60,
    ) as (_, client):
        bad = {"email": "ghost@example.com", "password": "anything-anything"}
        for _ in range(2):
            r = await client.post(LOGIN, json=bad)
            assert r.status_code == 401
        r = await client.post(LOGIN, json=bad)
        assert r.status_code == 429
