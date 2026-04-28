from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from regstack.backends.protocols import PendingAlreadyExistsError
from regstack.models.pending_registration import PendingRegistration

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


class PendingRepo:
    def __init__(self, db: AsyncDatabase, collection_name: str) -> None:
        self._collection = db[collection_name]

    async def upsert(self, pending: PendingRegistration) -> PendingRegistration:
        """Insert or replace the pending registration for this email.

        Resends overwrite an outstanding row so the most recent token is the
        only valid one — old links stop working as soon as a new one is sent.
        """
        doc = pending.to_mongo()
        result = await self._collection.find_one_and_replace(
            {"email": pending.email},
            doc,
            upsert=True,
            return_document=True,
        )
        if result is not None and "_id" in result:
            pending.id = str(result["_id"])
        return pending

    async def create(self, pending: PendingRegistration) -> PendingRegistration:
        try:
            result = await self._collection.insert_one(pending.to_mongo())
        except DuplicateKeyError as exc:
            raise PendingAlreadyExistsError(pending.email) from exc
        pending.id = str(result.inserted_id)
        return pending

    async def find_by_token_hash(self, token_hash: str) -> PendingRegistration | None:
        doc = await self._collection.find_one({"token_hash": token_hash})
        return self._hydrate(doc)

    async def find_by_email(self, email: str) -> PendingRegistration | None:
        doc = await self._collection.find_one({"email": email})
        return self._hydrate(doc)

    async def delete_by_id(self, pending_id: str) -> None:
        if not ObjectId.is_valid(pending_id):
            return
        await self._collection.delete_one({"_id": ObjectId(pending_id)})

    async def delete_by_email(self, email: str) -> None:
        await self._collection.delete_one({"email": email})

    async def purge_expired(self, now: datetime | None = None) -> int:
        """Manual reaper for callers that don't trust the TTL background sweep."""
        cutoff = now or datetime.now(UTC)
        result = await self._collection.delete_many({"expires_at": {"$lt": cutoff}})
        return int(result.deleted_count)

    @staticmethod
    def _hydrate(doc: dict[str, Any] | None) -> PendingRegistration | None:
        if doc is None:
            return None
        if isinstance(doc.get("_id"), ObjectId):
            doc["_id"] = str(doc["_id"])
        return PendingRegistration.model_validate(doc)
