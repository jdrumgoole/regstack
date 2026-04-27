from __future__ import annotations

import re
from datetime import timedelta

import pytest

from regstack.sms.null import NullSmsService

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"
MFA_CONFIRM = "/api/auth/login/mfa-confirm"
PHONE_START = "/api/auth/phone/start"
PHONE_CONFIRM = "/api/auth/phone/confirm"
PHONE_DELETE = "/api/auth/phone"

CREDS = {
    "email": "alice@example.com",
    "password": "hunter2hunter2",
    "full_name": "Alice",
}
PHONE = "+14155552671"


def _capture_sms(rs) -> NullSmsService:
    assert isinstance(rs.sms, NullSmsService)
    return rs.sms


_CODE_RE = re.compile(r"\b(\d{6})\b")


def _extract_code(body: str) -> str:
    m = _CODE_RE.search(body)
    assert m, f"no 6-digit code in body: {body!r}"
    return m.group(1)


async def _register_and_login(client) -> str:
    r = await client.post(REGISTER, json=CREDS)
    assert r.status_code == 201, r.text
    r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_phone_routes_unmounted_when_2fa_disabled(client) -> None:
    token = await _register_and_login(client)
    r = await client.post(
        PHONE_START,
        json={"phone_number": PHONE, "current_password": CREDS["password"]},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_phone_setup_round_trip_enables_mfa(make_client) -> None:
    async with make_client(enable_sms_2fa=True) as (rs, client):
        token = await _register_and_login(client)
        sms = _capture_sms(rs)
        sms.outbox.clear()

        r = await client.post(
            PHONE_START,
            json={"phone_number": PHONE, "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "code_sent"
        pending_token = body["pending_token"]

        assert len(sms.outbox) == 1
        msg = sms.outbox[0]
        assert msg.to == PHONE
        code = _extract_code(msg.body)

        r = await client.post(PHONE_CONFIRM, json={"pending_token": pending_token, "code": code})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["phone_number"] == PHONE
        assert body["is_mfa_enabled"] is True


@pytest.mark.asyncio
async def test_phone_start_rejects_bad_e164(make_client) -> None:
    async with make_client(enable_sms_2fa=True) as (_rs, client):
        token = await _register_and_login(client)
        r = await client.post(
            PHONE_START,
            json={"phone_number": "415-555-2671", "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_phone_start_rejects_wrong_password(make_client) -> None:
    async with make_client(enable_sms_2fa=True) as (_rs, client):
        token = await _register_and_login(client)
        r = await client.post(
            PHONE_START,
            json={"phone_number": PHONE, "current_password": "totally-wrong"},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_phone_disable_clears_state(make_client) -> None:
    async with make_client(enable_sms_2fa=True) as (rs, client):
        token = await _register_and_login(client)
        sms = _capture_sms(rs)

        # Set up MFA first.
        r = await client.post(
            PHONE_START,
            json={"phone_number": PHONE, "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        pending_token = r.json()["pending_token"]
        code = _extract_code(sms.outbox[-1].body)
        await client.post(PHONE_CONFIRM, json={"pending_token": pending_token, "code": code})

        r = await client.request(
            "DELETE",
            PHONE_DELETE,
            json={"current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        user = await rs.users.get_by_email(CREDS["email"])
        assert user is not None
        assert user.phone_number is None
        assert user.is_mfa_enabled is False


@pytest.mark.asyncio
async def test_login_with_mfa_returns_pending_token(make_client) -> None:
    async with make_client(enable_sms_2fa=True) as (rs, client):
        # Bootstrap: register, set up MFA via phone start → confirm.
        token = await _register_and_login(client)
        sms = _capture_sms(rs)
        start = await client.post(
            PHONE_START,
            json={"phone_number": PHONE, "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        pending_token = start.json()["pending_token"]
        setup_code = _extract_code(sms.outbox[-1].body)
        await client.post(
            PHONE_CONFIRM,
            json={"pending_token": pending_token, "code": setup_code},
        )

        # Now login should require MFA.
        sms.outbox.clear()
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "mfa_required"
        assert body["delivery"] == "sms"
        assert "access_token" not in body
        assert "mfa_pending_token" in body
        assert len(sms.outbox) == 1
        login_code = _extract_code(sms.outbox[0].body)
        assert sms.outbox[0].to == PHONE

        # Complete the second step.
        r = await client.post(
            MFA_CONFIRM,
            json={"mfa_pending_token": body["mfa_pending_token"], "code": login_code},
        )
        assert r.status_code == 200, r.text
        access = r.json()["access_token"]

        # Use the token: /me works.
        r = await client.get(ME, headers={"authorization": f"Bearer {access}"})
        assert r.status_code == 200
        assert r.json()["is_mfa_enabled"] is True


@pytest.mark.asyncio
async def test_mfa_wrong_code_then_lockout(make_client) -> None:
    async with make_client(enable_sms_2fa=True, sms_code_max_attempts=2) as (rs, client):
        token = await _register_and_login(client)
        sms = _capture_sms(rs)
        # Set up MFA
        r = await client.post(
            PHONE_START,
            json={"phone_number": PHONE, "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        pending = r.json()["pending_token"]
        setup_code = _extract_code(sms.outbox[-1].body)
        await client.post(PHONE_CONFIRM, json={"pending_token": pending, "code": setup_code})

        # Trigger an MFA login.
        sms.outbox.clear()
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        body = r.json()
        mfa_token = body["mfa_pending_token"]

        # First wrong attempt: WRONG with attempts remaining.
        r = await client.post(MFA_CONFIRM, json={"mfa_pending_token": mfa_token, "code": "000000"})
        assert r.status_code == 400
        assert "attempts remaining" in r.json()["detail"].lower()

        # Second wrong attempt: LOCKED, code consumed.
        r = await client.post(MFA_CONFIRM, json={"mfa_pending_token": mfa_token, "code": "111111"})
        assert r.status_code == 400
        # Even the correct code now misses (code was deleted on lockout).
        correct = _extract_code(sms.outbox[-1].body)
        r = await client.post(MFA_CONFIRM, json={"mfa_pending_token": mfa_token, "code": correct})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_mfa_pending_token_expires(make_client, frozen_clock) -> None:
    async with make_client(enable_sms_2fa=True, mfa_pending_token_ttl_seconds=120) as (rs, client):
        token = await _register_and_login(client)
        sms = _capture_sms(rs)
        r = await client.post(
            PHONE_START,
            json={"phone_number": PHONE, "current_password": CREDS["password"]},
            headers={"authorization": f"Bearer {token}"},
        )
        pending = r.json()["pending_token"]
        setup_code = _extract_code(sms.outbox[-1].body)
        await client.post(PHONE_CONFIRM, json={"pending_token": pending, "code": setup_code})

        sms.outbox.clear()
        r = await client.post(LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]})
        mfa_token = r.json()["mfa_pending_token"]

        frozen_clock.advance(timedelta(seconds=180))
        r = await client.post(MFA_CONFIRM, json={"mfa_pending_token": mfa_token, "code": "000000"})
        assert r.status_code == 400
        assert "invalid or has expired" in r.json()["detail"].lower()
