from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from regstack.backends.protocols import OAuthIdentityAlreadyLinkedError
from regstack.models.oauth_identity import OAuthIdentity

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


class MongoOAuthIdentityRepo:
    def __init__(self, db: AsyncDatabase, collection_name: str) -> None:
        self._collection = db[collection_name]

    async def create(self, identity: OAuthIdentity) -> OAuthIdentity:
        try:
            result = await self._collection.insert_one(identity.to_mongo())
        except DuplicateKeyError as exc:
            raise OAuthIdentityAlreadyLinkedError(
                f"{identity.provider}/{identity.subject_id}"
            ) from exc
        identity.id = str(result.inserted_id)
        return identity

    async def find_by_subject(self, *, provider: str, subject_id: str) -> OAuthIdentity | None:
        doc = await self._collection.find_one({"provider": provider, "subject_id": subject_id})
        return self._hydrate(doc)

    async def list_for_user(self, user_id: str) -> list[OAuthIdentity]:
        cursor = self._collection.find({"user_id": user_id}).sort("linked_at", 1)
        out: list[OAuthIdentity] = []
        async for doc in cursor:
            identity = self._hydrate(doc)
            if identity is not None:
                out.append(identity)
        return out

    async def delete(self, *, user_id: str, provider: str) -> bool:
        result = await self._collection.delete_one({"user_id": user_id, "provider": provider})
        return bool(result.deleted_count)

    async def delete_by_user_id(self, user_id: str) -> int:
        result = await self._collection.delete_many({"user_id": user_id})
        return int(result.deleted_count)

    async def touch_last_used(self, *, provider: str, subject_id: str, when: datetime) -> None:
        await self._collection.update_one(
            {"provider": provider, "subject_id": subject_id},
            {"$set": {"last_used_at": when}},
        )

    @staticmethod
    def _hydrate(doc: dict[str, Any] | None) -> OAuthIdentity | None:
        if doc is None:
            return None
        if isinstance(doc.get("_id"), ObjectId):
            doc["_id"] = str(doc["_id"])
        return OAuthIdentity.model_validate(doc)
