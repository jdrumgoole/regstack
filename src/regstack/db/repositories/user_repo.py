from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from regstack.auth.clock import Clock, SystemClock
from regstack.models.user import BaseUser

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


class UserAlreadyExistsError(Exception):
    """Raised when an attempt is made to insert a user with a duplicate email."""


def _bulk_revoke_cutoff(now: datetime) -> datetime:
    """The cutoff timestamp recorded on the user document. Stored at full
    microsecond precision; the JWT ``iat`` claim is also emitted as a float
    so the ``iat < cutoff`` comparison is exact and a fresh login completing
    even microseconds after a password / email change is recognised as
    later-than-cutoff.
    """
    return now


class UserRepo:
    def __init__(
        self,
        db: AsyncDatabase,
        collection_name: str,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._collection = db[collection_name]
        self._clock: Clock = clock or SystemClock()

    async def create(self, user: BaseUser) -> BaseUser:
        doc = user.to_mongo()
        try:
            result = await self._collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise UserAlreadyExistsError(user.email) from exc
        user.id = str(result.inserted_id)
        return user

    async def get_by_email(self, email: str) -> BaseUser | None:
        doc = await self._collection.find_one({"email": email})
        return self._hydrate(doc)

    async def get_by_id(self, user_id: str) -> BaseUser | None:
        if not ObjectId.is_valid(user_id):
            return None
        doc = await self._collection.find_one({"_id": ObjectId(user_id)})
        return self._hydrate(doc)

    async def set_last_login(self, user_id: str, when: datetime) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"last_login": when, "updated_at": self._clock.now()}},
        )

    async def set_tokens_invalidated_after(self, user_id: str, when: datetime) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "tokens_invalidated_after": _bulk_revoke_cutoff(when),
                    "updated_at": self._clock.now(),
                }
            },
        )

    async def update_password(self, user_id: str, hashed_password: str) -> None:
        now = self._clock.now()
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "hashed_password": hashed_password,
                    "tokens_invalidated_after": _bulk_revoke_cutoff(now),
                    "updated_at": now,
                }
            },
        )

    async def set_active(self, user_id: str, *, is_active: bool) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"is_active": is_active, "updated_at": self._clock.now()}},
        )

    async def set_superuser(self, user_id: str, *, is_superuser: bool) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"is_superuser": is_superuser, "updated_at": self._clock.now()}},
        )

    async def set_full_name(self, user_id: str, full_name: str | None) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"full_name": full_name, "updated_at": self._clock.now()}},
        )

    async def set_phone(self, user_id: str, phone_number: str | None) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"phone_number": phone_number, "updated_at": self._clock.now()}},
        )

    async def set_mfa_enabled(self, user_id: str, *, is_mfa_enabled: bool) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"is_mfa_enabled": is_mfa_enabled, "updated_at": self._clock.now()}},
        )

    async def update_email(self, user_id: str, new_email: str) -> None:
        """Atomically swap the user's email. Bumps tokens_invalidated_after so
        any session bound to the old email becomes useless.
        """
        now = self._clock.now()
        try:
            await self._collection.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "email": new_email,
                        "tokens_invalidated_after": _bulk_revoke_cutoff(now),
                        "updated_at": now,
                    }
                },
            )
        except DuplicateKeyError as exc:
            raise UserAlreadyExistsError(new_email) from exc

    async def delete(self, user_id: str) -> bool:
        if not ObjectId.is_valid(user_id):
            return False
        result = await self._collection.delete_one({"_id": ObjectId(user_id)})
        return bool(result.deleted_count)

    async def count(self, *, filter_: dict[str, Any] | None = None) -> int:
        return await self._collection.count_documents(filter_ or {})

    async def list_paged(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        sort: tuple[str, int] = ("created_at", -1),
    ) -> list[BaseUser]:
        cursor = self._collection.find().sort([sort]).skip(skip).limit(limit)
        out: list[BaseUser] = []
        async for doc in cursor:
            user = self._hydrate(doc)
            if user is not None:
                out.append(user)
        return out

    @staticmethod
    def _hydrate(doc: dict[str, Any] | None) -> BaseUser | None:
        if doc is None:
            return None
        if isinstance(doc.get("_id"), ObjectId):
            doc["_id"] = str(doc["_id"])
        return BaseUser.model_validate(doc)
