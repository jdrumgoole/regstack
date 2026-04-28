from __future__ import annotations

import re

import pytest

from regstack.email.console import ConsoleEmailService

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"
ADMIN_STATS = "/api/auth/admin/stats"
ADMIN_USERS = "/api/auth/admin/users"

ALICE = {"email": "alice@example.com", "password": "hunter2hunter2", "full_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "hunter2hunter2", "full_name": "Bob"}


def _extract_token(url: str) -> str:
    match = re.search(r"token=([^&\s]+)", url)
    assert match, f"no token in url: {url!r}"
    return match.group(1)


async def _login(client, email: str, password: str) -> str:
    r = await client.post(LOGIN, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_admin_router_unmounted_by_default(client) -> None:
    r = await client.get(ADMIN_STATS)
    assert r.status_code == 404  # router not mounted


@pytest.mark.asyncio
async def test_stats_requires_authentication(make_client) -> None:
    async with make_client(enable_admin_router=True) as (_, client):
        r = await client.get(ADMIN_STATS)
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_stats_requires_admin(make_client) -> None:
    async with make_client(enable_admin_router=True) as (_, client):
        await client.post(REGISTER, json=ALICE)
        token = await _login(client, ALICE["email"], ALICE["password"])
        r = await client.get(ADMIN_STATS, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_list_and_get_users(make_client) -> None:
    async with make_client(enable_admin_router=True) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        await client.post(REGISTER, json=ALICE)
        await client.post(REGISTER, json=BOB)

        token = await _login(client, "admin@example.com", "adminadminadmin")
        headers = {"authorization": f"Bearer {token}"}

        r = await client.get(ADMIN_STATS, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["total_users"] == 3
        assert body["superusers"] == 1

        r = await client.get(ADMIN_USERS, headers=headers)
        assert r.status_code == 200
        listing = r.json()
        assert listing["total"] == 3
        assert len(listing["items"]) == 3
        target_id = next(
            item["_id"] for item in listing["items"] if item["email"] == ALICE["email"]
        )

        r = await client.get(f"{ADMIN_USERS}/{target_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["email"] == ALICE["email"]


@pytest.mark.asyncio
async def test_admin_patch_user_disables_session(make_client) -> None:
    async with make_client(enable_admin_router=True) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        await client.post(REGISTER, json=ALICE)
        alice_token = await _login(client, ALICE["email"], ALICE["password"])
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")

        # /me works for Alice.
        r = await client.get(ME, headers={"authorization": f"Bearer {alice_token}"})
        assert r.status_code == 200

        alice = await rs.users.get_by_email(ALICE["email"])
        assert alice is not None

        # Admin disables Alice; her session must die immediately.
        r = await client.patch(
            f"{ADMIN_USERS}/{alice.id}",
            json={"is_active": False},
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is False

        r = await client.get(ME, headers={"authorization": f"Bearer {alice_token}"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_delete_user(make_client) -> None:
    async with make_client(enable_admin_router=True) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        await client.post(REGISTER, json=ALICE)
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")

        alice = await rs.users.get_by_email(ALICE["email"])
        assert alice is not None
        r = await client.delete(
            f"{ADMIN_USERS}/{alice.id}",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200
        assert await rs.users.get_by_email(ALICE["email"]) is None


@pytest.mark.asyncio
async def test_admin_resend_verification(make_client) -> None:
    async with make_client(enable_admin_router=True) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        # Create an unverified user directly via the repo so we can exercise the resend path.
        from regstack.models.user import BaseUser

        user = BaseUser(
            email="unverified@example.com",
            hashed_password=rs.password_hasher.hash("unverif1unverif1"),
            is_active=True,
            is_verified=False,
        )
        user = await rs.users.create(user)

        admin_token = await _login(client, "admin@example.com", "adminadminadmin")
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()

        r = await client.post(
            f"{ADMIN_USERS}/{user.id}/resend-verification",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 202, r.text
        assert len(rs.email.outbox) == 1
        assert rs.email.outbox[0].to == "unverified@example.com"

        # Already-verified user → 400.
        verified = await rs.bootstrap_admin("verified@example.com", "verifedverified")
        r = await client.post(
            f"{ADMIN_USERS}/{verified.id}/resend-verification",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_stats_pending_registrations_count_unexpired(make_client) -> None:
    """Admin stats must report pending-registration count correctly on every backend.

    Before this test landed, the stats route reached into Mongo's private
    ``_collection`` attribute and silently returned 0 on SQL backends. The
    parametrized ``backend_kind`` fixture means this asserts the count
    against SQLite, MongoDB, and PostgreSQL in turn — a gap that survived
    the multi-backend refactor because the existing admin tests didn't
    pin this number.

    Also seeds an already-expired pending row directly via the repo to
    confirm the count excludes it (the SQL backend has no TTL reaper, so
    this distinction matters).
    """
    from datetime import timedelta

    from regstack.models.pending_registration import PendingRegistration

    async with make_client(
        require_verification=True,
        enable_admin_router=True,
    ) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")
        headers = {"authorization": f"Bearer {admin_token}"}

        # Two fresh registrations → two unexpired pending rows.
        await client.post(REGISTER, json=ALICE)
        await client.post(REGISTER, json=BOB)

        r = await client.get(ADMIN_STATS, headers=headers)
        assert r.status_code == 200
        assert r.json()["pending_registrations"] == 2

        # Insert a stale pending row directly, anchored to ``rs.clock`` so
        # it's "in the past" from the route's POV (which also reads the
        # injected clock). Mongo would reap it via TTL eventually; SQL
        # leaves it in place until purge_expired runs. Either way,
        # count_unexpired must exclude it.
        stale = PendingRegistration(
            email="stale@example.com",
            hashed_password="x",
            full_name="Stale",
            token_hash="stale-token-hash",
            expires_at=rs.clock.now() - timedelta(hours=1),
        )
        await rs.pending.upsert(stale)

        r = await client.get(ADMIN_STATS, headers=headers)
        assert r.status_code == 200
        assert r.json()["pending_registrations"] == 2, (
            f"stale row should not be counted: {r.json()}"
        )


@pytest.mark.asyncio
async def test_admin_404_for_unknown_user(make_client) -> None:
    async with make_client(enable_admin_router=True) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")
        # 24-char hex but not present in the database.
        bogus = "ffffffffffffffffffffffff"
        r = await client.get(
            f"{ADMIN_USERS}/{bogus}",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 404
