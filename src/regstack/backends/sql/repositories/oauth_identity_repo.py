from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, select, update
from sqlalchemy.exc import IntegrityError

from regstack.backends.protocols import OAuthIdentityAlreadyLinkedError
from regstack.backends.sql.schema import oauth_identities_table
from regstack.models.oauth_identity import OAuthIdentity

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _row_to_identity(row) -> OAuthIdentity:
    data = dict(row._mapping)
    return OAuthIdentity.model_validate(data)


class SqlOAuthIdentityRepo:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._t = oauth_identities_table

    async def create(self, identity: OAuthIdentity) -> OAuthIdentity:
        if identity.id is None:
            identity.id = uuid.uuid4().hex
        if identity.linked_at is None:
            identity.linked_at = datetime.now(UTC)
        values = {
            "id": identity.id,
            "user_id": identity.user_id,
            "provider": identity.provider,
            "subject_id": identity.subject_id,
            "email": identity.email,
            "linked_at": identity.linked_at,
            "last_used_at": identity.last_used_at,
        }
        try:
            async with self._engine.begin() as conn:
                await conn.execute(self._t.insert().values(values))
        except IntegrityError as exc:
            raise OAuthIdentityAlreadyLinkedError(
                f"{identity.provider}/{identity.subject_id}"
            ) from exc
        return identity

    async def find_by_subject(self, *, provider: str, subject_id: str) -> OAuthIdentity | None:
        stmt = select(self._t).where(
            and_(self._t.c.provider == provider, self._t.c.subject_id == subject_id)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).first()
        return _row_to_identity(row) if row else None

    async def list_for_user(self, user_id: str) -> list[OAuthIdentity]:
        stmt = (
            select(self._t).where(self._t.c.user_id == user_id).order_by(self._t.c.linked_at.asc())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_identity(r) for r in rows]

    async def delete(self, *, user_id: str, provider: str) -> bool:
        stmt = delete(self._t).where(
            and_(self._t.c.user_id == user_id, self._t.c.provider == provider)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount or 0)

    async def delete_by_user_id(self, user_id: str) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(self._t).where(self._t.c.user_id == user_id))
        return int(result.rowcount or 0)

    async def touch_last_used(self, *, provider: str, subject_id: str, when: datetime) -> None:
        stmt = (
            update(self._t)
            .where(and_(self._t.c.provider == provider, self._t.c.subject_id == subject_id))
            .values(last_used_at=when)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
