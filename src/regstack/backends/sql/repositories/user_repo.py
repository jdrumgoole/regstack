from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, desc, func, select, update
from sqlalchemy.exc import IntegrityError

from regstack.backends.protocols import UserAlreadyExistsError
from regstack.backends.sql.schema import users_table
from regstack.models.user import BaseUser

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from regstack.auth.clock import Clock


def _row_to_user(row) -> BaseUser:
    data = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    return BaseUser.model_validate(data)


class SqlUserRepo:
    """SQLAlchemy 2 async implementation of UserRepoProtocol."""

    def __init__(self, engine: AsyncEngine, *, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock
        self._t = users_table

    async def create(self, user: BaseUser) -> BaseUser:
        if user.id is None:
            user.id = uuid.uuid4().hex
        now = self._clock.now()
        user.created_at = user.created_at or now
        user.updated_at = user.updated_at or now
        values = _user_values(user)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(self._t.insert().values(values))
        except IntegrityError as exc:
            raise UserAlreadyExistsError(user.email) from exc
        return user

    async def get_by_email(self, email: str) -> BaseUser | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(self._t).where(self._t.c.email == email))).first()
        return _row_to_user(row) if row else None

    async def get_by_id(self, user_id: str) -> BaseUser | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(self._t).where(self._t.c.id == user_id))).first()
        return _row_to_user(row) if row else None

    async def set_last_login(self, user_id: str, when: datetime) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(last_login=when, updated_at=self._clock.now())
            )

    async def set_tokens_invalidated_after(self, user_id: str, when: datetime) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(tokens_invalidated_after=when, updated_at=self._clock.now())
            )

    async def update_password(self, user_id: str, hashed_password: str) -> None:
        now = self._clock.now()
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(
                    hashed_password=hashed_password,
                    tokens_invalidated_after=now,
                    updated_at=now,
                )
            )

    async def set_active(self, user_id: str, *, is_active: bool) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(is_active=is_active, updated_at=self._clock.now())
            )

    async def set_superuser(self, user_id: str, *, is_superuser: bool) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(is_superuser=is_superuser, updated_at=self._clock.now())
            )

    async def set_full_name(self, user_id: str, full_name: str | None) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(full_name=full_name, updated_at=self._clock.now())
            )

    async def set_phone(self, user_id: str, phone_number: str | None) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(phone_number=phone_number, updated_at=self._clock.now())
            )

    async def set_mfa_enabled(self, user_id: str, *, is_mfa_enabled: bool) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(self._t)
                .where(self._t.c.id == user_id)
                .values(is_mfa_enabled=is_mfa_enabled, updated_at=self._clock.now())
            )

    async def update_email(self, user_id: str, new_email: str) -> None:
        now = self._clock.now()
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    update(self._t)
                    .where(self._t.c.id == user_id)
                    .values(
                        email=new_email,
                        tokens_invalidated_after=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as exc:
            raise UserAlreadyExistsError(new_email) from exc

    async def delete(self, user_id: str) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(delete(self._t).where(self._t.c.id == user_id))
        return bool(result.rowcount)

    async def count(
        self,
        *,
        is_active: bool | None = None,
        is_verified: bool | None = None,
        is_superuser: bool | None = None,
    ) -> int:
        clauses = []
        if is_active is not None:
            clauses.append(self._t.c.is_active == is_active)
        if is_verified is not None:
            clauses.append(self._t.c.is_verified == is_verified)
        if is_superuser is not None:
            clauses.append(self._t.c.is_superuser == is_superuser)
        stmt = select(func.count()).select_from(self._t)
        if clauses:
            stmt = stmt.where(and_(*clauses))
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
        return int(result.scalar() or 0)

    async def list_paged(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        sort_by_created_at_desc: bool = True,
    ) -> list[BaseUser]:
        order = desc(self._t.c.created_at) if sort_by_created_at_desc else self._t.c.created_at
        stmt = select(self._t).order_by(order).offset(skip).limit(limit)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_user(r) for r in rows]


def _user_values(user: BaseUser) -> dict:
    """Materialise a BaseUser into a column-value dict suitable for insert."""
    return {
        "id": user.id,
        "email": user.email,
        "hashed_password": user.hashed_password,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "is_superuser": user.is_superuser,
        "full_name": user.full_name,
        "phone_number": user.phone_number,
        "is_mfa_enabled": user.is_mfa_enabled,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "last_login": user.last_login,
        "tokens_invalidated_after": user.tokens_invalidated_after,
    }
