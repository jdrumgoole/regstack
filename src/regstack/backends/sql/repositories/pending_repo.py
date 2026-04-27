from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from regstack.backends.sql.schema import pending_table
from regstack.models.pending_registration import PendingRegistration

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _row_to_pending(row) -> PendingRegistration:
    data = dict(row._mapping)
    return PendingRegistration.model_validate(data)


class SqlPendingRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._t = pending_table

    async def upsert(self, pending: PendingRegistration) -> PendingRegistration:
        if pending.id is None:
            pending.id = uuid.uuid4().hex
        if pending.created_at is None:
            pending.created_at = datetime.now(UTC)
        values = _pending_values(pending)
        async with self._engine.begin() as conn:
            await conn.execute(delete(self._t).where(self._t.c.email == pending.email))
            await conn.execute(self._t.insert().values(values))
        return pending

    async def find_by_token_hash(self, token_hash: str) -> PendingRegistration | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(select(self._t).where(self._t.c.token_hash == token_hash))
            ).first()
        return _row_to_pending(row) if row else None

    async def find_by_email(self, email: str) -> PendingRegistration | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(self._t).where(self._t.c.email == email))).first()
        return _row_to_pending(row) if row else None

    async def delete_by_email(self, email: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(self._t).where(self._t.c.email == email))

    async def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now or datetime.now(UTC)
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(self._t).where(self._t.c.expires_at < cutoff))
        return int(result.rowcount or 0)


def _pending_values(p: PendingRegistration) -> dict:
    return {
        "id": p.id,
        "email": p.email,
        "hashed_password": p.hashed_password,
        "full_name": p.full_name,
        "token_hash": p.token_hash,
        "created_at": p.created_at,
        "expires_at": p.expires_at,
    }
