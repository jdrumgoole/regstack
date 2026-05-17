"""Tests for ``RegStack.promote_pending`` + the admin route that wraps it.

Exercises the bypass path that lets an admin (or a programmatic
caller like a CLI) convert a PendingRegistration row directly into
a verified user without going through the email-link round-trip.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from regstack import RegStack
from regstack.backends.protocols import UserAlreadyExistsError

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"

ALICE = {"email": "alice@example.com", "password": "hunter2hunter2", "full_name": "Alice"}


async def _login(client: AsyncClient, email: str, password: str) -> str:
    r = await client.post(LOGIN, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_promote_pending_creates_verified_active_user(make_client) -> None:
    """The bypass produces the same shape of user as POST /verify:
    is_active=True, is_verified=True, same email + full_name + hashed_password.
    """
    async with make_client(require_verification=True) as (rs, client):
        r = await client.post(REGISTER, json=ALICE)
        # require_verification=True returns 201 + pending response
        assert r.status_code == 201, r.text

        user = await rs.promote_pending(ALICE["email"])
        assert user.email == ALICE["email"]
        assert user.full_name == ALICE["full_name"]
        assert user.is_active is True
        assert user.is_verified is True


@pytest.mark.asyncio
async def test_promote_pending_user_can_log_in_with_original_password(
    make_client,
) -> None:
    async with make_client(require_verification=True) as (rs, client):
        await client.post(REGISTER, json=ALICE)
        await rs.promote_pending(ALICE["email"])

        # User logs in with the password they originally registered with.
        token = await _login(client, ALICE["email"], ALICE["password"])
        r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["email"] == ALICE["email"]


@pytest.mark.asyncio
async def test_promote_pending_deletes_pending_row(make_client) -> None:
    async with make_client(require_verification=True) as (rs, client):
        await client.post(REGISTER, json=ALICE)
        await rs.promote_pending(ALICE["email"])

        # No pending row left → /resend-verification returns the
        # generic anti-enumeration ack; the row itself is gone.
        assert await rs.pending.find_by_email(ALICE["email"]) is None


@pytest.mark.asyncio
async def test_promote_pending_fires_user_verified_hook(make_client) -> None:
    """Downstream listeners (analytics, audit log, marketing) should
    see the same `user_verified` event the email-driven /verify
    route fires — promotion is just a different trigger.
    """
    payloads: list[dict] = []

    async with make_client(require_verification=True) as (rs, client):
        rs.hooks.on("user_verified", lambda **kw: payloads.append(kw))
        await client.post(REGISTER, json=ALICE)
        await rs.promote_pending(ALICE["email"])

    assert len(payloads) == 1
    assert payloads[0]["user"].email == ALICE["email"]


@pytest.mark.asyncio
async def test_promote_pending_no_pending_row_raises_lookup_error(
    make_client,
) -> None:
    async with make_client(require_verification=True) as (rs, _client):
        with pytest.raises(LookupError):
            await rs.promote_pending("ghost@example.com")


@pytest.mark.asyncio
async def test_promote_pending_email_collision_raises(make_client) -> None:
    """If a verified user already exists with that email (perhaps the
    user previously registered through another path), the create
    must raise — the caller decides how to surface the conflict.
    """
    async with make_client(require_verification=True) as (rs, client):
        await client.post(REGISTER, json=ALICE)
        # First promotion lands the user.
        await rs.promote_pending(ALICE["email"])
        # Now re-register so a fresh pending row exists alongside the
        # already-promoted user. (Some pending repos upsert by email;
        # the test still works because the second promote will find
        # *some* pending row and then collide on the users-collection
        # uniqueness constraint.)
        from datetime import timedelta

        from regstack.models.pending_registration import PendingRegistration

        await rs.pending.upsert(
            PendingRegistration(
                email=ALICE["email"],
                hashed_password="dummy",
                full_name=ALICE["full_name"],
                token_hash="some-hash",
                expires_at=rs.clock.now() + timedelta(hours=1),
            )
        )

        with pytest.raises(UserAlreadyExistsError):
            await rs.promote_pending(ALICE["email"])


# --- Admin route wrapper ----------------------------------------------

ADMIN_PROMOTE = "/api/auth/admin/pending/{email}/promote"


@pytest.mark.asyncio
async def test_admin_promote_pending_route_201(make_client) -> None:
    async with make_client(
        require_verification=True,
        enable_admin_router=True,
    ) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")

        await client.post(REGISTER, json=ALICE)

        r = await client.post(
            ADMIN_PROMOTE.format(email=ALICE["email"]),
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["email"] == ALICE["email"]
        assert body["is_verified"] is True


@pytest.mark.asyncio
async def test_admin_promote_pending_route_requires_admin(make_client) -> None:
    async with make_client(
        require_verification=True,
        enable_admin_router=True,
    ) as (rs, client):
        await client.post(REGISTER, json=ALICE)

        # No auth at all → 401
        r = await client.post(ADMIN_PROMOTE.format(email=ALICE["email"]))
        assert r.status_code == 401

        # Authenticated but non-admin → 403. We need another non-pending
        # account to log in as.
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        # bootstrap_admin produces an active+verified user with that
        # password. Demote them so login succeeds but admin check fails.
        from regstack.models.user import BaseUser

        admin: BaseUser | None = await rs.users.get_by_email("admin@example.com")
        assert admin is not None and admin.id is not None
        await rs.users.set_superuser(admin.id, is_superuser=False)

        token = await _login(client, "admin@example.com", "adminadminadmin")
        r = await client.post(
            ADMIN_PROMOTE.format(email=ALICE["email"]),
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_promote_pending_route_404_when_no_pending(make_client) -> None:
    async with make_client(enable_admin_router=True) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")

        r = await client.post(
            ADMIN_PROMOTE.format(email="ghost@example.com"),
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_admin_promote_pending_route_409_when_user_exists(make_client) -> None:
    async with make_client(
        require_verification=True,
        enable_admin_router=True,
    ) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")

        await client.post(REGISTER, json=ALICE)
        # First promotion succeeds.
        r = await client.post(
            ADMIN_PROMOTE.format(email=ALICE["email"]),
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 201

        # Re-seed pending so the second attempt has a row to find, then collide.
        from datetime import timedelta

        from regstack.models.pending_registration import PendingRegistration

        await rs.pending.upsert(
            PendingRegistration(
                email=ALICE["email"],
                hashed_password="dummy",
                full_name=ALICE["full_name"],
                token_hash="another-hash",
                expires_at=rs.clock.now() + timedelta(hours=1),
            )
        )
        r = await client.post(
            ADMIN_PROMOTE.format(email=ALICE["email"]),
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 409
