from __future__ import annotations

import contextlib
from datetime import datetime
from typing import TYPE_CHECKING

from pymongo.errors import DuplicateKeyError

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


class BlacklistRepo:
    """Per-token revocation store. The `exp` field has a TTL index that
    auto-reaps documents once the underlying token would have expired anyway.
    """

    def __init__(self, db: AsyncDatabase, collection_name: str) -> None:
        self._collection = db[collection_name]

    async def revoke(self, jti: str, exp: datetime) -> None:
        # Idempotent — re-revoking the same jti is a no-op.
        with contextlib.suppress(DuplicateKeyError):
            await self._collection.insert_one({"jti": jti, "exp": exp})

    async def is_revoked(self, jti: str) -> bool:
        doc = await self._collection.find_one({"jti": jti}, projection={"_id": 1})
        return doc is not None
