from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, select, update

from regstack.auth.tokens import hash_token
from regstack.backends.mongo.repositories.mfa_code_repo import (
    MfaVerifyOutcome,
    MfaVerifyResult,
)
from regstack.backends.sql.schema import mfa_codes_table
from regstack.models.mfa_code import MfaCode, MfaKind

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from regstack.auth.clock import Clock


class SqlMfaCodeRepo:
    def __init__(self, engine: AsyncEngine, *, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
        self._t = mfa_codes_table

    async def put(self, code: MfaCode) -> None:
        if code.created_at is None:
            code.created_at = datetime.now(UTC)
        async with self._engine.begin() as conn:
            # Upsert by (user_id, kind) — overwrite any outstanding row so a
            # re-issued code invalidates the old one.
            await conn.execute(
                delete(self._t).where(
                    and_(self._t.c.user_id == code.user_id, self._t.c.kind == code.kind)
                )
            )
            await conn.execute(
                self._t.insert().values(
                    user_id=code.user_id,
                    kind=code.kind,
                    code_hash=code.code_hash,
                    expires_at=code.expires_at,
                    attempts=code.attempts,
                    max_attempts=code.max_attempts,
                    created_at=code.created_at,
                )
            )

    async def verify(
        self,
        *,
        user_id: str,
        kind: MfaKind,
        raw_code: str,
    ) -> MfaVerifyResult:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(self._t).where(
                        and_(self._t.c.user_id == user_id, self._t.c.kind == kind)
                    )
                )
            ).first()
        if row is None:
            return MfaVerifyResult(MfaVerifyOutcome.MISSING)
        attempts = int(row.attempts)
        max_attempts = int(row.max_attempts)
        if attempts >= max_attempts:
            await self._delete_row(row.id)
            return MfaVerifyResult(MfaVerifyOutcome.LOCKED)
        if row.expires_at <= self._clock.now():
            await self._delete_row(row.id)
            return MfaVerifyResult(MfaVerifyOutcome.EXPIRED)
        if row.code_hash != hash_token(raw_code):
            new_attempts = attempts + 1
            async with self._engine.begin() as conn:
                await conn.execute(
                    update(self._t).where(self._t.c.id == row.id).values(attempts=new_attempts)
                )
            remaining = max(max_attempts - new_attempts, 0)
            if remaining == 0:
                await self._delete_row(row.id)
                return MfaVerifyResult(MfaVerifyOutcome.LOCKED)
            return MfaVerifyResult(MfaVerifyOutcome.WRONG, attempts_remaining=remaining)
        await self._delete_row(row.id)
        return MfaVerifyResult(MfaVerifyOutcome.OK)

    async def delete(self, *, user_id: str, kind: MfaKind | None = None) -> None:
        clauses = [self._t.c.user_id == user_id]
        if kind is not None:
            clauses.append(self._t.c.kind == kind)
        async with self._engine.begin() as conn:
            await conn.execute(delete(self._t).where(and_(*clauses)))

    async def find(self, *, user_id: str, kind: MfaKind) -> MfaCode | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(self._t).where(
                        and_(self._t.c.user_id == user_id, self._t.c.kind == kind)
                    )
                )
            ).first()
        if row is None:
            return None
        return MfaCode.model_validate(dict(row._mapping))

    async def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now or datetime.now(UTC)
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(self._t).where(self._t.c.expires_at < cutoff))
        return int(result.rowcount or 0)

    async def _delete_row(self, row_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(self._t).where(self._t.c.id == row_id))
