from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update

from regstack.backends.sql.schema import oauth_states_table
from regstack.models.oauth_state import OAuthState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _row_to_state(row) -> OAuthState:
    data = dict(row._mapping)
    return OAuthState.model_validate(data)


class SqlOAuthStateRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._t = oauth_states_table

    async def create(self, state: OAuthState) -> None:
        values = {
            "id": state.id,
            "provider": state.provider,
            "code_verifier": state.code_verifier,
            "nonce": state.nonce,
            "redirect_to": state.redirect_to,
            "mode": state.mode,
            "linking_user_id": state.linking_user_id,
            "created_at": state.created_at,
            "expires_at": state.expires_at,
            "result_token": state.result_token,
        }
        async with self._engine.begin() as conn:
            await conn.execute(self._t.insert().values(values))

    async def find(self, state_id: str) -> OAuthState | None:
        stmt = select(self._t).where(self._t.c.id == state_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return _row_to_state(row) if row else None

    async def set_result_token(self, state_id: str, token: str) -> None:
        stmt = update(self._t).where(self._t.c.id == state_id).values(result_token=token)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def consume(self, state_id: str) -> OAuthState | None:
        # Read + delete in one transaction so a second concurrent
        # /oauth/exchange call sees the row as already-gone.
        async with self._engine.begin() as conn:
            row = (await conn.execute(select(self._t).where(self._t.c.id == state_id))).first()
            if row is None:
                return None
            await conn.execute(delete(self._t).where(self._t.c.id == state_id))
        return _row_to_state(row)

    async def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now or datetime.now(UTC)
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(self._t).where(self._t.c.expires_at < cutoff))
        return int(result.rowcount or 0)
