"""Every hook event that carries an emailed link also carries a redacted
copy of it, so a host handler that logs its ``**kwargs`` has a safe field
to reach for. See ``docs/architecture.md`` (Hooks) and issue #154.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from regstack.email.console import ConsoleEmailService
from regstack.hooks import REDACTED
from regstack.models.user import BaseUser

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
RESEND = "/api/auth/resend-verification"
FORGOT = "/api/auth/forgot-password"
CHANGE_EMAIL = "/api/auth/change-email"
ADMIN_USERS = "/api/auth/admin/users"

CREDS = {"email": "alice@example.com", "password": "hunter2hunter2", "full_name": "Alice"}
NEW_EMAIL = "alice2@example.com"


def _extract_token(url: str) -> str:
    match = re.search(r"token=([^&\s]+)", url)
    assert match, f"no token in url: {url!r}"
    return match.group(1)


def _assert_redacted(payload: dict[str, Any], token: str) -> None:
    """The raw `url` still carries the token; `url_without_token` doesn't."""
    assert token in payload["url"]
    safe = payload["url_without_token"]
    assert token not in safe
    assert REDACTED in safe
    assert safe == payload["url"].replace(token, REDACTED)


async def _login(client, email: str, password: str) -> str:
    r = await client.post(LOGIN, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_verification_requested_on_register(make_client) -> None:
    payloads: list[dict[str, Any]] = []
    async with make_client(require_verification=True) as (rs, client):
        rs.hooks.on("verification_requested", lambda **kw: payloads.append(kw))
        r = await client.post(REGISTER, json=CREDS)
        assert r.status_code == 201, r.text

        assert isinstance(rs.email, ConsoleEmailService)
        token = _extract_token(rs.email.outbox[0].text)
        assert len(payloads) == 1
        _assert_redacted(payloads[0], token)


@pytest.mark.asyncio
async def test_verification_requested_on_resend(make_client) -> None:
    payloads: list[dict[str, Any]] = []
    async with make_client(require_verification=True) as (rs, client):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()
        rs.hooks.on("verification_requested", lambda **kw: payloads.append(kw))

        r = await client.post(RESEND, json={"email": CREDS["email"]})
        assert r.status_code == 202, r.text

        token = _extract_token(rs.email.outbox[0].text)
        assert len(payloads) == 1
        _assert_redacted(payloads[0], token)


@pytest.mark.asyncio
async def test_password_reset_requested(make_client) -> None:
    payloads: list[dict[str, Any]] = []
    async with make_client() as (rs, client):
        await client.post(REGISTER, json=CREDS)
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()
        rs.hooks.on("password_reset_requested", lambda **kw: payloads.append(kw))

        r = await client.post(FORGOT, json={"email": CREDS["email"]})
        assert r.status_code == 202, r.text

        token = _extract_token(rs.email.outbox[0].text)
        assert len(payloads) == 1
        _assert_redacted(payloads[0], token)


@pytest.mark.asyncio
async def test_email_change_requested(make_client) -> None:
    payloads: list[dict[str, Any]] = []
    async with make_client() as (rs, client):
        await client.post(REGISTER, json=CREDS)
        access = await _login(client, CREDS["email"], CREDS["password"])
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()
        rs.hooks.on("email_change_requested", lambda **kw: payloads.append(kw))

        r = await client.post(
            CHANGE_EMAIL,
            json={"current_password": CREDS["password"], "new_email": NEW_EMAIL},
            headers={"authorization": f"Bearer {access}"},
        )
        assert r.status_code == 202, r.text

        token = _extract_token(rs.email.outbox[0].text)
        assert len(payloads) == 1
        _assert_redacted(payloads[0], token)


@pytest.mark.asyncio
async def test_admin_resend_verification(make_client) -> None:
    payloads: list[dict[str, Any]] = []
    async with make_client(enable_admin_router=True) as (rs, client):
        await rs.bootstrap_admin("admin@example.com", "adminadminadmin")
        user = await rs.users.create(
            BaseUser(
                email="unverified@example.com",
                hashed_password=rs.password_hasher.hash("unverif1unverif1"),
                is_active=True,
                is_verified=False,
            )
        )
        admin_token = await _login(client, "admin@example.com", "adminadminadmin")
        assert isinstance(rs.email, ConsoleEmailService)
        rs.email.outbox.clear()
        rs.hooks.on("verification_requested", lambda **kw: payloads.append(kw))

        r = await client.post(
            f"{ADMIN_USERS}/{user.id}/resend-verification",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 202, r.text

        token = _extract_token(rs.email.outbox[0].text)
        assert len(payloads) == 1
        _assert_redacted(payloads[0], token)


@pytest.mark.asyncio
async def test_redaction_holds_for_hash_routed_templates(make_client) -> None:
    """A host that moves the token out of the query string with
    `verify_url_template` still gets a token-free `url_without_token`.
    """
    payloads: list[dict[str, Any]] = []
    async with make_client(
        require_verification=True,
        verify_url_template="{base_url}/#/verify/{token}",
    ) as (rs, client):
        rs.hooks.on("verification_requested", lambda **kw: payloads.append(kw))
        r = await client.post(REGISTER, json=CREDS)
        assert r.status_code == 201, r.text

        assert len(payloads) == 1
        url = payloads[0]["url"]
        assert "/#/verify/" in url
        token = url.rsplit("/", 1)[1]
        _assert_redacted(payloads[0], token)
