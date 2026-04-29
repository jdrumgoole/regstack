from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import create_async_engine

from regstack.backends.base import Backend, BackendKind
from regstack.backends.sql.migrations import upgrade_async
from regstack.backends.sql.repositories.blacklist_repo import SqlBlacklistRepo
from regstack.backends.sql.repositories.login_attempt_repo import SqlLoginAttemptRepo
from regstack.backends.sql.repositories.mfa_code_repo import SqlMfaCodeRepo
from regstack.backends.sql.repositories.oauth_identity_repo import (
    SqlOAuthIdentityRepo,
)
from regstack.backends.sql.repositories.oauth_state_repo import SqlOAuthStateRepo
from regstack.backends.sql.repositories.pending_repo import SqlPendingRepo
from regstack.backends.sql.repositories.user_repo import SqlUserRepo

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
        self.oauth_identities = SqlOAuthIdentityRepo(self._engine)
        self.oauth_states = SqlOAuthStateRepo(self._engine)

    async def install_schema(self) -> None:
        """Run the bundled Alembic migrations to head.

        Idempotent — Alembic's `upgrade` is a no-op when the database
        is already at the target revision. Safe to call from a FastAPI
        ``lifespan`` startup on every boot.

        Hosts that need to drive migrations from CI / a deploy step
        instead of in-process can call ``regstack migrate`` (CLI) or
        ``regstack.backends.sql.migrations.upgrade(database_url)``
        (programmatic) and skip ``install_schema()``.
        """
        await upgrade_async(self.config.database_url.get_secret_value())

    async def aclose(self) -> None:
        await self._engine.dispose()

    async def ping(self) -> None:
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    @property
    def engine(self) -> AsyncEngine:
        return self._engine
