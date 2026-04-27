from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from regstack.backends.sql.schema import blacklist_table

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


class SqlBlacklistRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._t = blacklist_table

    async def revoke(self, jti: str, exp: datetime) -> None:
        # Idempotent — re-revoking is a no-op.
        with contextlib.suppress(IntegrityError):
            async with self._engine.begin() as conn:
                await conn.execute(self._t.insert().values(jti=jti, exp=exp))

    async def is_revoked(self, jti: str) -> bool:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(select(self._t.c.id).where(self._t.c.jti == jti))
            ).first()
        return row is not None

    async def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now or datetime.now(UTC)
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(self._t).where(self._t.c.exp < cutoff))
        return int(result.rowcount or 0)
