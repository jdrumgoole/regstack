from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, func, select

from regstack.backends.sql.schema import login_attempts_table

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


class SqlLoginAttemptRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._t = login_attempts_table

    async def record_failure(
        self, email: str, *, when: datetime | None = None, ip: str | None = None
    ) -> None:
        ts = when or datetime.now(UTC)
        async with self._engine.begin() as conn:
            await conn.execute(self._t.insert().values(email=email, when=ts, ip=ip))

    async def count_recent(self, email: str, *, window: timedelta, now: datetime) -> int:
        cutoff = now - window
        stmt = (
            select(func.count())
            .select_from(self._t)
            .where(and_(self._t.c.email == email, self._t.c.when >= cutoff))
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
        return int(result.scalar() or 0)

    async def clear(self, email: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(self._t).where(self._t.c.email == email))

    async def purge_expired(self, now: datetime, window: timedelta) -> int:
        cutoff = now - window
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(self._t).where(self._t.c.when < cutoff))
        return int(result.rowcount or 0)
