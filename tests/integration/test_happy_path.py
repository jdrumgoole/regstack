from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient

from regstack import RegStack
from regstack.auth.clock import FrozenClock

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"
LOGOUT = "/api/auth/logout"

CREDS = {
    "email": "alice@example.com",
    "password": "hunter2hunter2",
    "full_name": "Alice Example",
}


@pytest.mark.asyncio
async def test_register_login_me_logout(client: AsyncClient, regstack: RegStack) -> None:
    r = await client.post(REGISTER, json=CREDS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == CREDS["email"]
    assert body["is_active"] is True
    assert "_id" in body or "id" in body  # alias

    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    assert token

    r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == CREDS["email"]

    r = await client.post(LOGOUT, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text

    # Token should now be revoked
    r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_duplicate_registration_rejected(client: AsyncClient) -> None:
    await client.post(REGISTER, json=CREDS)
    r = await client.post(REGISTER, json=CREDS)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_login_with_wrong_password(client: AsyncClient) -> None:
    await client.post(REGISTER, json=CREDS)
    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": "nope-nope-nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_for_unknown_user(client: AsyncClient) -> None:
    r = await client.post(LOGIN, json={"email": "ghost@example.com", "password": "doesntmatter1"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    r = await client.get(ME)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_password_too_short_rejected(client: AsyncClient) -> None:
    r = await client.post(
        REGISTER, json={"email": "x@example.com", "password": "short", "full_name": "x"}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_token_expires(
    client: AsyncClient, regstack: RegStack, frozen_clock: FrozenClock
) -> None:
    await client.post(REGISTER, json=CREDS)
    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
    token = r.json()["access_token"]

    frozen_clock.advance(timedelta(seconds=regstack.config.jwt_ttl_seconds + 5))

    r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bulk_revocation_via_password_change(
    client: AsyncClient, regstack: RegStack, frozen_clock: FrozenClock
) -> None:
    await client.post(REGISTER, json=CREDS)
    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
    token = r.json()["access_token"]

    user = await regstack.users.get_by_email(CREDS["email"])
    assert user is not None and user.id is not None

    # Simulate a password-change side effect: bump the bulk-invalidation cutoff.
    frozen_clock.advance(timedelta(seconds=1))
    await regstack.users.set_tokens_invalidated_after(user.id, frozen_clock.now())

    r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_inactive_user_cannot_login(client: AsyncClient, regstack: RegStack) -> None:
    await client.post(REGISTER, json=CREDS)
    user = await regstack.users.get_by_email(CREDS["email"])
    assert user is not None and user.id is not None
    await regstack.users.set_active(user.id, is_active=False)

    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_bootstrap_admin(regstack: RegStack) -> None:
    user = await regstack.bootstrap_admin("admin@example.com", "admin-password-9")
    assert user.is_superuser
    assert user.is_verified
    # Idempotent
    again = await regstack.bootstrap_admin("admin@example.com", "ignored")
    assert again.id == user.id
