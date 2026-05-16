"""Regression test for the ObjectId.is_valid() guards on UserRepo mutations.

Before this fix, calling any of nine mutation methods (set_last_login,
set_tokens_invalidated_after, update_password, set_active, set_superuser,
set_full_name, set_phone, set_mfa_enabled, update_email) with a string
that is not a valid ObjectId raised ``bson.errors.InvalidId`` — which
would surface to the FastAPI layer as a 500. Every existing call site
in the routers happens to pre-validate via ``get_by_id``, so no current
endpoint is exploitable, but the defensive guard ensures a future
caller with raw external input can't trigger that path.

Flagged as I-1 in the 2026-05-15 / 2026-05-16 security reviews.

This test is mongo-only by design — the guards are specific to the
ObjectId-keyed Mongo schema. The SQL backend keys users by integer or
UUID and has its own type-coercion checks.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from regstack.backends.mongo.repositories.user_repo import UserRepo


@pytest.fixture
def backend_kind() -> str:
    """Pin to the Mongo backend; this test does not run under SQL."""
    return "mongo"


@pytest_asyncio.fixture
async def user_repo(mongo_client):
    """Fresh per-test users collection inside the legacy mongo_client DB."""
    coll_name = f"users_objectid_guard_{secrets.token_hex(4)}"
    db = mongo_client.get_default_database()
    repo = UserRepo(db, coll_name)
    yield repo
    await db[coll_name].drop()


# Mongo ObjectId is exactly 12 bytes = 24 hex chars; anything else is invalid.
_INVALID_IDS = [
    "",
    "not-an-objectid",
    "deadbeef",  # too short
    "g" * 24,  # right length, non-hex
    "1234567890",
    "../../../etc/passwd",  # path-traversal probe
    "'; DROP TABLE users; --",  # sqli probe (Mongo is immune but the guard still applies)
]


@pytest.mark.parametrize("bad_id", _INVALID_IDS)
async def test_mutation_methods_silently_no_op_on_invalid_id(
    user_repo: UserRepo, bad_id: str
) -> None:
    """Each of the nine guarded mutations returns cleanly on invalid input.

    Before the fix, these raised ``bson.errors.InvalidId``. The contract
    matches ``get_by_id``/``delete``: invalid input → no-op return.
    """
    now = datetime.now(tz=UTC)

    # Each call must NOT raise. The collection has no docs so a "valid"
    # ObjectId would also no-op — the regression we're guarding against is
    # the ObjectId constructor itself raising on malformed input.
    await user_repo.set_last_login(bad_id, now)
    await user_repo.set_tokens_invalidated_after(bad_id, now)
    await user_repo.update_password(bad_id, "fake-hash")
    await user_repo.set_active(bad_id, is_active=False)
    await user_repo.set_superuser(bad_id, is_superuser=True)
    await user_repo.set_full_name(bad_id, "Probe")
    await user_repo.set_phone(bad_id, "+15555550100")
    await user_repo.set_mfa_enabled(bad_id, is_mfa_enabled=True)
    await user_repo.update_email(bad_id, "probe@example.com")


async def test_mutations_still_work_with_valid_id(user_repo: UserRepo) -> None:
    """Sanity check that the guard didn't break the happy path."""
    from regstack.models.user import BaseUser

    user = await user_repo.create(
        BaseUser(
            email=f"probe-{secrets.token_hex(4)}@example.com",
            hashed_password="fake-hash",
            full_name=None,
            is_verified=True,
        )
    )
    assert user.id is not None

    now = datetime.now(tz=UTC)
    await user_repo.set_last_login(user.id, now)
    await user_repo.set_full_name(user.id, "Updated Probe")

    after = await user_repo.get_by_id(user.id)
    assert after is not None
    assert after.full_name == "Updated Probe"
    assert after.last_login is not None
    assert after.last_login - now < timedelta(seconds=1)
