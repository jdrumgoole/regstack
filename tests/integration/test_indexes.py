from __future__ import annotations

import pytest
from pymongo.errors import DuplicateKeyError

from regstack import RegStack
from regstack.models.user import BaseUser


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
