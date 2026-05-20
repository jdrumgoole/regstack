"""Mongo-specific index behaviour. Forced to backend_kind=mongo via the
local fixture override so the parametrized backend_kind fixture in the
top-level conftest doesn't generate a [sqlite] variant.

Cross-backend "duplicate emails are rejected" assertions live in the
integration suite (test_happy_path) where they verify the
UserAlreadyExistsError contract — that's the protocol-level guarantee.
"""

from __future__ import annotations

import pytest
from pymongo.errors import DuplicateKeyError

from regstack import RegStack
from regstack.models.user import BaseUser


@pytest.fixture
def backend_kind() -> str:
    return "mongo"


@pytest.mark.asyncio
async def test_email_unique_index_blocks_duplicates(regstack: RegStack) -> None:
    user = BaseUser(email="dup@example.com", hashed_password="h")
    await regstack.users.create(user)
    other = BaseUser(email="dup@example.com", hashed_password="h")
    with pytest.raises(Exception) as exc:
        # raw insert via repo to bypass the friendly UserAlreadyExistsError wrap
        await regstack.users._collection.insert_one(other.to_mongo())  # type: ignore[attr-defined]
    assert isinstance(exc.value, DuplicateKeyError)


@pytest.mark.asyncio
async def test_blacklist_index_idempotent(regstack: RegStack) -> None:
    from datetime import UTC, datetime, timedelta

    exp = datetime.now(UTC) + timedelta(hours=1)
    await regstack.blacklist.revoke("jti-1", exp)
    await regstack.blacklist.revoke("jti-1", exp)  # second insert silently ignored
    assert await regstack.blacklist.is_revoked("jti-1")
    assert not await regstack.blacklist.is_revoked("never-issued")


@pytest.mark.asyncio
async def test_install_indexes_renames_legacy_unnamed_email_unique(
    regstack: RegStack,
) -> None:
    """A host that adopted regstack from its own auth code may have
    previously created an unnamed unique index on ``email`` (default
    Mongo name ``email_1``). On the next boot under regstack,
    ``install_indexes`` must drop the legacy index and replace it
    with the canonical ``email_unique`` rather than raising
    ``IndexOptionsConflict``. Regression for the winebox migration
    that hit this on its first OAT deploy at v0.7.81.
    """
    from pymongo import ASCENDING

    backend = regstack.backend
    users = backend.database[regstack.config.user_collection]  # type: ignore[attr-defined]

    # Wipe the canonical index regstack just created, then plant a
    # legacy-style unnamed unique index on the same key.
    await users.drop_indexes()
    legacy_name = await users.create_index([("email", ASCENDING)], unique=True)
    assert legacy_name == "email_1", f"Mongo default-naming changed under us — saw {legacy_name!r}"

    # Re-running install_indexes must reconcile rather than crash.
    await regstack.install_schema()

    info = await users.index_information()
    assert "email_unique" in info, (
        f"email_unique should have been (re)created; got {list(info.keys())}"
    )
    assert info["email_unique"]["key"] == [("email", 1)]
    assert info["email_unique"]["unique"] is True
    assert "email_1" not in info, (
        f"Legacy email_1 should have been dropped; got {list(info.keys())}"
    )


@pytest.mark.asyncio
async def test_install_indexes_is_a_noop_on_canonical_state(
    regstack: RegStack,
) -> None:
    """If the canonical email_unique already exists, re-running
    install_indexes must not drop and recreate it (which would
    interrupt readers and trigger a needless O(n) build).
    """
    backend = regstack.backend
    users = backend.database[regstack.config.user_collection]  # type: ignore[attr-defined]

    info_before = await users.index_information()
    assert "email_unique" in info_before
    await regstack.install_schema()
    info_after = await users.index_information()
    assert info_after.keys() == info_before.keys()


@pytest.mark.asyncio
async def test_doctor_mongo_server_version_check_runs_against_live_server(
    regstack: RegStack,
) -> None:
    """End-to-end wiring for the CVE-2025-14847 advisory (security review
    2026-05-20 · I-3): the check must reach the live server via
    ``buildInfo`` and produce a ``mongo server`` result. The local CI
    mongo is a recent, patched release, so we expect a non-failing
    result — but the point of the test is that the buildInfo path works,
    not the verdict."""
    from regstack.cli.doctor import _check_mongo_server_version

    result = await _check_mongo_server_version(regstack.config)
    assert result is not None
    assert result.name == "mongo server"
    # Recent server → not a hard failure. ``warn`` may be True only if the
    # CI image is somehow on an affected build; either way it must not be
    # a hard failure (ok stays True for advisory results).
    assert result.ok is True
    assert "server" in result.detail
