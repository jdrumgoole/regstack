from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from regstack.auth.tokens import hash_token
from regstack.models.mfa_code import MfaCode, MfaKind

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

    from regstack.auth.clock import Clock


class MfaVerifyOutcome(StrEnum):
    OK = "ok"
    WRONG = "wrong"
    EXPIRED = "expired"
    LOCKED = "locked"
    MISSING = "missing"


@dataclass(slots=True, frozen=True)
class MfaVerifyResult:
    outcome: MfaVerifyOutcome
    attempts_remaining: int = 0


class MfaCodeRepo:
    def __init__(self, db: AsyncDatabase, collection_name: str, *, clock: Clock) -> None:
        self._collection = db[collection_name]
        self._clock = clock

    async def put(self, code: MfaCode) -> None:
        """Upsert by ``(user_id, kind)`` — re-issuing a code overwrites
        any previous outstanding code, so old SMS messages stop working
        as soon as a new one is sent.
        """
        doc = code.to_mongo()
        await self._collection.find_one_and_replace(
            {"user_id": code.user_id, "kind": code.kind},
            doc,
            upsert=True,
        )

    async def verify(
        self,
        *,
        user_id: str,
        kind: MfaKind,
        raw_code: str,
    ) -> MfaVerifyResult:
        doc = await self._collection.find_one({"user_id": user_id, "kind": kind})
        if doc is None:
            return MfaVerifyResult(MfaVerifyOutcome.MISSING)
        attempts = int(doc.get("attempts", 0))
        max_attempts = int(doc.get("max_attempts", 5))
        if attempts >= max_attempts:
            await self._collection.delete_one({"_id": doc["_id"]})
            return MfaVerifyResult(MfaVerifyOutcome.LOCKED)

        if doc["expires_at"] <= self._clock.now():
            await self._collection.delete_one({"_id": doc["_id"]})
            return MfaVerifyResult(MfaVerifyOutcome.EXPIRED)

        if doc["code_hash"] != hash_token(raw_code):
            new_attempts = attempts + 1
            await self._collection.update_one(
                {"_id": doc["_id"]}, {"$set": {"attempts": new_attempts}}
            )
            remaining = max(max_attempts - new_attempts, 0)
            if remaining == 0:
                await self._collection.delete_one({"_id": doc["_id"]})
                return MfaVerifyResult(MfaVerifyOutcome.LOCKED)
            return MfaVerifyResult(MfaVerifyOutcome.WRONG, attempts_remaining=remaining)

        await self._collection.delete_one({"_id": doc["_id"]})
        return MfaVerifyResult(MfaVerifyOutcome.OK)

    async def delete(self, *, user_id: str, kind: MfaKind | None = None) -> None:
        query: dict[str, object] = {"user_id": user_id}
        if kind is not None:
            query["kind"] = kind
        await self._collection.delete_many(query)

    async def find(self, *, user_id: str, kind: MfaKind) -> dict[str, object] | None:
        return await self._collection.find_one({"user_id": user_id, "kind": kind})

    @staticmethod
    def make_code_hash(raw_code: str) -> str:
        return hash_token(raw_code)


def now_plus_seconds(clock: Clock, seconds: int) -> datetime:
    from datetime import timedelta

    return clock.now() + timedelta(seconds=seconds)
