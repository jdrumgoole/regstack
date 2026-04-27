from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import create_async_engine

from regstack.backends.base import Backend, BackendKind
from regstack.backends.sql.repositories.blacklist_repo import SqlBlacklistRepo
from regstack.backends.sql.repositories.login_attempt_repo import SqlLoginAttemptRepo
from regstack.backends.sql.repositories.mfa_code_repo import SqlMfaCodeRepo
from regstack.backends.sql.repositories.pending_repo import SqlPendingRepo
from regstack.backends.sql.repositories.user_repo import SqlUserRepo
from regstack.backends.sql.schema import metadata

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from regstack.auth.clock import Clock
    from regstack.config.schema import RegStackConfig


class SqlBackend(Backend):
    """SQLAlchemy 2 async backend. Same code path drives SQLite (via
    aiosqlite) and Postgres (via asyncpg) — only ``database_url`` differs.
    """

    def __init__(
        self,
        *,
        config: RegStackConfig,
        clock: Clock,
        kind: BackendKind,
    ) -> None:
        super().__init__(config=config, clock=clock)
        if kind not in (BackendKind.SQLITE, BackendKind.POSTGRES):
            raise ValueError(f"SqlBackend does not support kind={kind}")
        self.kind = kind
        self._engine: AsyncEngine = create_async_engine(
            config.database_url.get_secret_value(),
            future=True,
        )
        self.users = SqlUserRepo(self._engine, clock=clock)
        self.pending = SqlPendingRepo(self._engine)
        self.blacklist = SqlBlacklistRepo(self._engine)
        self.attempts = SqlLoginAttemptRepo(self._engine)
        self.mfa_codes = SqlMfaCodeRepo(self._engine, clock=clock)

    async def install_schema(self) -> None:
        """Create all tables. Idempotent thanks to SQLAlchemy's
        ``checkfirst=True`` default. We deliberately skip Alembic for
        the in-process create so a fresh SQLite file is usable without
        running ``alembic upgrade head`` first; production callers on
        Postgres should still drive migrations through Alembic for
        evolutions, but the initial create is fine either way.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def aclose(self) -> None:
        await self._engine.dispose()

    async def ping(self) -> None:
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    @property
    def engine(self) -> AsyncEngine:
        return self._engine
