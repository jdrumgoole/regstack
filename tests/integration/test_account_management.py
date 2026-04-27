from __future__ import annotations

import re
from datetime import timedelta

import pytest

from regstack.email.console import ConsoleEmailService

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"
CHANGE_PASSWORD = "/api/auth/change-password"
CHANGE_EMAIL = "/api/auth/change-email"
CONFIRM_EMAIL_CHANGE = "/api/auth/confirm-email-change"
DELETE_ACCOUNT = "/api/auth/account"

CREDS = {
    "email": "alice@example.com",
    "password": "hunter2hunter2",
    "full_name": "Alice",
}
NEW_PASSWORD = "newhunter3newhunter3"
NEW_EMAIL = "alice2@example.com"


def _extract_token(url: str) -> str:
    match = re.search(r"token=([^&\s]+)", url)
    assert match, f"no token in url: {url!r}"
    return match.group(1)


async def _register_and_login(client) -> str:
    r = await client.post(REGISTER, json=CREDS)
    assert r.status_code == 201, r.text
    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_update_profile(client) -> None:
    token = await _register_and_login(client)
    r = await client.patch(
        ME,
        json={"full_name": "Alice X."},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["full_name"] == "Alice X."


@pytest.mark.asyncio
async def test_change_password_happy_path(client) -> None:
    token = await _register_and_login(client)
    r = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": CREDS["password"], "new_password": NEW_PASSWORD},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    # Old session is invalidated.
    r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 401

    # Old password rejected; new password works.
    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
    assert r.status_code == 401
    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": NEW_PASSWORD})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_change_password_wrong_current_pw(client) -> None:
    token = await _register_and_login(client)
    r = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": "totally-wrong-pw", "new_password": NEW_PASSWORD},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_change_password_must_differ(client) -> None:
    token = await _register_and_login(client)
    r = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": CREDS["password"], "new_password": CREDS["password"]},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_change_email_round_trip(make_client) -> None:
    async with make_client() as (rs, client):
        token = await _register_and_login(client)
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()

        r = await client.post(
            CHANGE_EMAIL,
            json={"new_email": NEW_EMAIL, "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        assert len(rs.email.outbox) == 1
        message = rs.email.outbox[0]
        assert message.to == NEW_EMAIL  # confirmation goes to NEW address
        change_token = _extract_token(message.text)

        r = await client.post(CONFIRM_EMAIL_CHANGE, json={"token": change_token})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == NEW_EMAIL

        # Old session is invalidated; new email logs in.
        r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 401
        r = await client.post(LOGIN, json={"email": NEW_EMAIL, "password": CREDS["password"]})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_login_immediately_after_password_change_is_valid(make_client, frozen_clock) -> None:
    """Regression: a login issued microseconds after a bulk-revoke cutoff must
    still validate. JWT ``iat`` is emitted as a float (RFC 7519 NumericDate)
    so the ``iat < cutoff`` comparison is exact; without that, an
    integer-truncated iat would sit at or below a microsecond cutoff stored
    in the same wall-clock second.
    """
    from datetime import timedelta as _td

    async with make_client() as (_rs, client):
        token = await _register_and_login(client)
        assert (
            await client.get(ME, headers={"authorization": f"Bearer {token}"})
        ).status_code == 200

        # Bulk-revoke fires inside change-password.
        r = await client.post(
            CHANGE_PASSWORD,
            json={"current_password": CREDS["password"], "new_password": NEW_PASSWORD},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert (
            await client.get(ME, headers={"authorization": f"Bearer {token}"})
        ).status_code == 401

        # Advance the clock just enough that the new login's iat is strictly
        # greater than the cutoff (which was written at the change-password
        # instant). Even sub-millisecond is enough thanks to float iat.
        frozen_clock.advance(_td(microseconds=1))
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": NEW_PASSWORD})
        assert r.status_code == 200, r.text
        new_token = r.json()["access_token"]
        r = await client.get(ME, headers={"authorization": f"Bearer {new_token}"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_change_email_rejects_taken_address(client) -> None:
    # Register two users; user A tries to take user B's email.
    await client.post(
        REGISTER, json={"email": "b@example.com", "password": "passpasspass1", "full_name": "B"}
    )
    token = await _register_and_login(client)
    r = await client.post(
        CHANGE_EMAIL,
        json={"new_email": "b@example.com", "current_password": CREDS["password"]},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_change_email_rejects_wrong_password(client) -> None:
    token = await _register_and_login(client)
    r = await client.post(
        CHANGE_EMAIL,
        json={"new_email": NEW_EMAIL, "current_password": "totally-wrong"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_change_email_rejects_same_email(client) -> None:
    token = await _register_and_login(client)
    r = await client.post(
        CHANGE_EMAIL,
        json={"new_email": CREDS["email"], "current_password": CREDS["password"]},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_change_email_token_expires(make_client, frozen_clock) -> None:
    async with make_client(email_change_token_ttl_seconds=120) as (rs, client):
        token = await _register_and_login(client)
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()
        r = await client.post(
            CHANGE_EMAIL,
            json={"new_email": NEW_EMAIL, "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202
        change_token = _extract_token(rs.email.outbox[0].text)

        frozen_clock.advance(timedelta(seconds=180))
        r = await client.post(CONFIRM_EMAIL_CHANGE, json={"token": change_token})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_change_email_token_purpose_separation(client) -> None:
    """A regular session JWT must not satisfy the confirm-email endpoint."""
    token = await _register_and_login(client)
    r = await client.post(CONFIRM_EMAIL_CHANGE, json={"token": token})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_account_happy_path(make_client) -> None:
    async with make_client() as (rs, client):
        token = await _register_and_login(client)
        # Add some lockout failures and a pending row to verify cleanup.
        await rs.attempts.record_failure(CREDS["email"])
        from datetime import timedelta as _td

        from regstack.models.pending_registration import PendingRegistration

        await rs.pending.upsert(
            PendingRegistration(
                email=CREDS["email"],
                hashed_password="x",
                token_hash="x",
                expires_at=rs.clock.now() + _td(seconds=60),
            )
        )

        r = await client.request(
            "DELETE",
            DELETE_ACCOUNT,
            json={"current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text

        # User is gone.
        assert await rs.users.get_by_email(CREDS["email"]) is None
        # Pending row cleaned up.
        assert await rs.pending.find_by_email(CREDS["email"]) is None
        # Login attempts cleaned up.
        from datetime import timedelta as _td2

        assert (
            await rs.attempts.count_recent(CREDS["email"], window=_td2(hours=1), now=rs.clock.now())
            == 0
        )

        # Old session token can no longer authenticate.
        r = await client.get(ME, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_requires_password(client) -> None:
    token = await _register_and_login(client)
    r = await client.request(
        "DELETE",
        DELETE_ACCOUNT,
        json={"current_password": "totally-wrong"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_account_disabled_returns_404(make_client) -> None:
    async with make_client(enable_account_deletion=False) as (_, client):
        token = await _register_and_login(client)
        r = await client.request(
            "DELETE",
            DELETE_ACCOUNT,
            json={"current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404
