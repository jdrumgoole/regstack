"""Integration tests for the OAuth identity + state repos.

Runs against every active backend via the parametrized
``backend_kind`` fixture in ``tests/conftest.py``. This is the
release-gate for M2 — if any of these fail on a backend, that
backend's repo is broken and OAuth login will return 0
linked-identities or fail to look up state rows on callback.
"""

from __future__ import annotations

import secrets as _secrets
from datetime import UTC, datetime, timedelta

import pytest

from regstack.backends.protocols import OAuthIdentityAlreadyLinkedError
from regstack.models.oauth_identity import OAuthIdentity
from regstack.models.oauth_state import OAuthState
from regstack.models.user import BaseUser

# ---------------------------------------------------------------------------
# Identity repo
# ---------------------------------------------------------------------------


async def _make_user(rs, email: str = "alice@example.com") -> BaseUser:
    user = BaseUser(
        email=email,
        hashed_password=rs.password_hasher.hash("hunter2hunter2"),
        is_active=True,
        is_verified=True,
    )
    return await rs.users.create(user)


@pytest.mark.asyncio
async def test_identity_create_and_lookup(regstack) -> None:
    user = await _make_user(regstack)
    assert user.id is not None

    identity = OAuthIdentity(
        user_id=user.id,
        provider="google",
        subject_id="goog-sub-001",
        email=user.email,
    )
    created = await regstack.oauth_identities.create(identity)
    assert created.id is not None

    found = await regstack.oauth_identities.find_by_subject(
        provider="google", subject_id="goog-sub-001"
    )
    assert found is not None
    assert found.user_id == user.id
    assert found.email == user.email


@pytest.mark.asyncio
async def test_identity_unique_provider_subject(regstack) -> None:
    """Two regstack users cannot both be linked to the same Google account."""
    a = await _make_user(regstack, "a@example.com")
    b = await _make_user(regstack, "b@example.com")
    assert a.id is not None and b.id is not None

    await regstack.oauth_identities.create(
        OAuthIdentity(user_id=a.id, provider="google", subject_id="shared-sub")
    )
    with pytest.raises(OAuthIdentityAlreadyLinkedError):
        await regstack.oauth_identities.create(
            OAuthIdentity(user_id=b.id, provider="google", subject_id="shared-sub")
        )


@pytest.mark.asyncio
async def test_identity_unique_user_provider(regstack) -> None:
    """One regstack user cannot link two Google accounts."""
    user = await _make_user(regstack)
    assert user.id is not None

    await regstack.oauth_identities.create(
        OAuthIdentity(user_id=user.id, provider="google", subject_id="sub-1")
    )
    with pytest.raises(OAuthIdentityAlreadyLinkedError):
        await regstack.oauth_identities.create(
            OAuthIdentity(user_id=user.id, provider="google", subject_id="sub-2")
        )


@pytest.mark.asyncio
async def test_identity_list_for_user_sorted(regstack) -> None:
    """list_for_user returns the linked identities in linked_at order."""
    user = await _make_user(regstack)
    assert user.id is not None

    early = OAuthIdentity(
        user_id=user.id,
        provider="google",
        subject_id="g-1",
        linked_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    later = OAuthIdentity(
        user_id=user.id,
        provider="github",  # different provider -> doesn't violate uniqueness
        subject_id="gh-1",
        linked_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    await regstack.oauth_identities.create(early)
    await regstack.oauth_identities.create(later)

    rows = await regstack.oauth_identities.list_for_user(user.id)
    assert [r.provider for r in rows] == ["google", "github"]


@pytest.mark.asyncio
async def test_identity_delete_one(regstack) -> None:
    user = await _make_user(regstack)
    assert user.id is not None
    await regstack.oauth_identities.create(
        OAuthIdentity(user_id=user.id, provider="google", subject_id="g-1")
    )

    removed = await regstack.oauth_identities.delete(user_id=user.id, provider="google")
    assert removed is True

    # Idempotent — second delete returns False, not an exception.
    again = await regstack.oauth_identities.delete(user_id=user.id, provider="google")
    assert again is False
    assert (
        await regstack.oauth_identities.find_by_subject(provider="google", subject_id="g-1") is None
    )


@pytest.mark.asyncio
async def test_identity_delete_by_user_id_cascades(regstack) -> None:
    user = await _make_user(regstack)
    assert user.id is not None

    await regstack.oauth_identities.create(
        OAuthIdentity(user_id=user.id, provider="google", subject_id="g")
    )
    await regstack.oauth_identities.create(
        OAuthIdentity(user_id=user.id, provider="github", subject_id="h")
    )

    removed = await regstack.oauth_identities.delete_by_user_id(user.id)
    assert removed == 2
    assert await regstack.oauth_identities.list_for_user(user.id) == []


@pytest.mark.asyncio
async def test_identity_touch_last_used(regstack) -> None:
    user = await _make_user(regstack)
    assert user.id is not None
    await regstack.oauth_identities.create(
        OAuthIdentity(user_id=user.id, provider="google", subject_id="g")
    )
    assert (
        await regstack.oauth_identities.find_by_subject(provider="google", subject_id="g")
    ).last_used_at is None

    when = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    await regstack.oauth_identities.touch_last_used(provider="google", subject_id="g", when=when)

    refreshed = await regstack.oauth_identities.find_by_subject(provider="google", subject_id="g")
    assert refreshed is not None
    assert refreshed.last_used_at == when


# ---------------------------------------------------------------------------
# State repo
# ---------------------------------------------------------------------------


def _state_row(**overrides) -> OAuthState:
    defaults = dict(
        id=_secrets.token_urlsafe(32),
        provider="google",
        code_verifier=_secrets.token_urlsafe(32),
        nonce=_secrets.token_urlsafe(16),
        redirect_to="/account/me",
        mode="signin",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    defaults.update(overrides)
    return OAuthState(**defaults)


@pytest.mark.asyncio
async def test_state_create_and_find(regstack) -> None:
    state = _state_row()
    await regstack.oauth_states.create(state)

    found = await regstack.oauth_states.find(state.id)
    assert found is not None
    assert found.code_verifier == state.code_verifier
    assert found.mode == "signin"
    assert found.result_token is None


@pytest.mark.asyncio
async def test_state_set_result_token(regstack) -> None:
    state = _state_row()
    await regstack.oauth_states.create(state)

    await regstack.oauth_states.set_result_token(state.id, "the-session-jwt")

    found = await regstack.oauth_states.find(state.id)
    assert found is not None
    assert found.result_token == "the-session-jwt"


@pytest.mark.asyncio
async def test_state_consume_is_single_use(regstack) -> None:
    """consume() returns the row once, then the row is gone."""
    state = _state_row()
    await regstack.oauth_states.create(state)
    await regstack.oauth_states.set_result_token(state.id, "the-jwt")

    first = await regstack.oauth_states.consume(state.id)
    assert first is not None
    assert first.result_token == "the-jwt"

    again = await regstack.oauth_states.consume(state.id)
    assert again is None


@pytest.mark.asyncio
async def test_state_consume_missing(regstack) -> None:
    """consume() on an unknown id returns None, not an exception."""
    assert await regstack.oauth_states.consume("does-not-exist") is None


@pytest.mark.asyncio
async def test_state_purge_expired(regstack) -> None:
    """purge_expired drops rows past their TTL."""
    fresh = _state_row(expires_at=datetime.now(UTC) + timedelta(minutes=5))
    stale = _state_row(expires_at=datetime.now(UTC) - timedelta(minutes=5))
    await regstack.oauth_states.create(fresh)
    await regstack.oauth_states.create(stale)

    removed = await regstack.oauth_states.purge_expired()
    assert removed == 1
    assert await regstack.oauth_states.find(stale.id) is None
    assert await regstack.oauth_states.find(fresh.id) is not None


# ---------------------------------------------------------------------------
# users.hashed_password is now nullable — confirm it round-trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_hashed_password_can_be_null(regstack) -> None:
    """OAuth-only users have no password. The repo must persist that."""
    user = BaseUser(
        email="oauth-only@example.com",
        hashed_password=None,
        is_active=True,
        is_verified=True,
    )
    created = await regstack.users.create(user)
    assert created.id is not None

    found = await regstack.users.get_by_email("oauth-only@example.com")
    assert found is not None
    assert found.hashed_password is None
