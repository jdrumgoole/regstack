from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from regstack.models.oauth_state import OAuthState

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


class MongoOAuthStateRepo:
    def __init__(self, db: AsyncDatabase, collection_name: str) -> None:
        self._collection = db[collection_name]

    async def create(self, state: OAuthState) -> None:
        # We use the caller-supplied id as _id directly so consume() can
        # delete by primary key without needing a separate index.
        doc = state.to_mongo()
        await self._collection.insert_one(doc)

    async def find(self, state_id: str) -> OAuthState | None:
        doc = await self._collection.find_one({"_id": state_id})
        return self._hydrate(doc)

    async def set_result_token(self, state_id: str, token: str) -> None:
        await self._collection.update_one(
            {"_id": state_id},
            {"$set": {"result_token": token}},
        )

    async def consume(self, state_id: str) -> OAuthState | None:
        doc = await self._collection.find_one_and_delete({"_id": state_id})
        return self._hydrate(doc)

    async def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now or datetime.now(UTC)
        result = await self._collection.delete_many({"expires_at": {"$lt": cutoff}})
        return int(result.deleted_count)

    @staticmethod
    def _hydrate(doc: dict[str, Any] | None) -> OAuthState | None:
        if doc is None:
            return None
        return OAuthState.model_validate(doc)
