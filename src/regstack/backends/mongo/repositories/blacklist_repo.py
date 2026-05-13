from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pymongo.errors import DuplicateKeyError

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

    from regstack.backends.mongo.client import MongoDoc


class BlacklistRepo:
    """Per-token revocation store. The `exp` field has a TTL index that
    auto-reaps documents once the underlying token would have expired anyway.
    """

    def __init__(self, db: AsyncDatabase[MongoDoc], collection_name: str) -> None:
        self._collection = db[collection_name]

    async def revoke(self, jti: str, exp: datetime) -> None:
        # Idempotent — re-revoking the same jti is a no-op.
        with contextlib.suppress(DuplicateKeyError):
            await self._collection.insert_one({"jti": jti, "exp": exp})

    async def is_revoked(self, jti: str) -> bool:
        doc = await self._collection.find_one({"jti": jti}, projection={"_id": 1})
        return doc is not None

    async def purge_expired(self, now: datetime | None = None) -> int:
        # MongoDB's TTL index on `exp` reaps documents automatically, but the
        # protocol requires this method so the SQL backends and Mongo present
        # the same interface. Run an explicit `delete_many` as a belt-and-braces
        # sweep — useful in tests and on Mongos where the TTL monitor's 60-second
        # cycle hasn't fired yet.
        reference = now or datetime.now(UTC)
        # Strict `<` matches the rest of the purge_expired family across both
        # backends — a token whose exp is the reference instant is still
        # nominally valid for one more microsecond.
        result = await self._collection.delete_many({"exp": {"$lt": reference}})
        return int(result.deleted_count)
