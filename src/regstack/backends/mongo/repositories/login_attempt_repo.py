from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from regstack.models.login_attempt import LoginAttempt

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


class LoginAttemptRepo:
    def __init__(self, db: AsyncDatabase, collection_name: str) -> None:
        self._collection = db[collection_name]

    async def record_failure(
        self, email: str, *, when: datetime | None = None, ip: str | None = None
    ) -> None:
        attempt = LoginAttempt(email=email, when=when or datetime.now(UTC), ip=ip)
        await self._collection.insert_one(attempt.to_mongo())

    async def count_recent(self, email: str, *, window: timedelta, now: datetime) -> int:
        cutoff = now - window
        return await self._collection.count_documents({"email": email, "when": {"$gte": cutoff}})

    async def clear(self, email: str) -> None:
        await self._collection.delete_many({"email": email})

    async def purge_expired(self, now: datetime, window: timedelta) -> int:
        """Manual reaper for protocol parity with SQL backends."""
        cutoff = now - window
        result = await self._collection.delete_many({"when": {"$lt": cutoff}})
        return int(result.deleted_count)
