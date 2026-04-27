"""Storage-layer protocols.

Each backend (`mongo`, `sql`) provides one concrete implementation per
protocol. Routers and services depend only on these protocols, so a
backend swap is a wiring change, not a code change.

Mongo's existing semantics are the contract: anything new (SQLAlchemy,
in-memory, etc.) is judged by behavioural parity, not by whether the SQL
or Mongo idiom feels "right" to the implementer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from regstack.models.mfa_code import MfaCode, MfaKind
from regstack.models.pending_registration import PendingRegistration
from regstack.models.user import BaseUser


class UserAlreadyExistsError(Exception):
    """Raised when an attempt is made to insert a user with a duplicate email,
    or to set an email that another user already owns.

    Lifted out of the Mongo repo so SQL backends can raise the same type
    on their own integrity-error paths.
    """


@runtime_checkable
class UserRepoProtocol(Protocol):
    async def create(self, user: BaseUser) -> BaseUser: ...

    async def get_by_email(self, email: str) -> BaseUser | None: ...

    async def get_by_id(self, user_id: str) -> BaseUser | None: ...

    async def set_last_login(self, user_id: str, when: datetime) -> None: ...

    async def set_tokens_invalidated_after(self, user_id: str, when: datetime) -> None: ...

    async def update_password(self, user_id: str, hashed_password: str) -> None: ...

    async def set_active(self, user_id: str, *, is_active: bool) -> None: ...

    async def set_superuser(self, user_id: str, *, is_superuser: bool) -> None: ...

    async def set_full_name(self, user_id: str, full_name: str | None) -> None: ...

    async def set_phone(self, user_id: str, phone_number: str | None) -> None: ...

    async def set_mfa_enabled(self, user_id: str, *, is_mfa_enabled: bool) -> None: ...

    async def update_email(self, user_id: str, new_email: str) -> None:
        """Atomically swap email + bump tokens_invalidated_after.

        Implementations MUST raise :class:`UserAlreadyExistsError` if the
        new email is already taken by a different user. Bulk-revoke is
        the caller-visible side-effect that sessions bound to the old
        email die immediately.
        """
        ...

    async def delete(self, user_id: str) -> bool: ...

    async def count(
        self,
        *,
        is_active: bool | None = None,
        is_verified: bool | None = None,
        is_superuser: bool | None = None,
    ) -> int:
        """Count users matching ALL of the provided filters (None = ignored)."""
        ...

    async def list_paged(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        sort_by_created_at_desc: bool = True,
    ) -> list[BaseUser]: ...


@runtime_checkable
class PendingRepoProtocol(Protocol):
    async def upsert(self, pending: PendingRegistration) -> PendingRegistration:
        """Insert or replace the pending registration for this email.

        Resends overwrite outstanding rows so the most recent token is
        the only valid one — old verification links must stop working.
        """
        ...

    async def find_by_token_hash(self, token_hash: str) -> PendingRegistration | None: ...

    async def find_by_email(self, email: str) -> PendingRegistration | None: ...

    async def delete_by_email(self, email: str) -> None: ...

    async def purge_expired(self, now: datetime | None = None) -> int:
        """Sweep expired rows. MongoDB has a TTL index; SQL backends rely
        on a periodic call to this method.
        """
        ...


@runtime_checkable
class BlacklistRepoProtocol(Protocol):
    async def revoke(self, jti: str, exp: datetime) -> None: ...

    async def is_revoked(self, jti: str) -> bool: ...

    async def purge_expired(self, now: datetime | None = None) -> int: ...


@runtime_checkable
class LoginAttemptRepoProtocol(Protocol):
    async def record_failure(
        self,
        email: str,
        *,
        when: datetime | None = None,
        ip: str | None = None,
    ) -> None: ...

    async def count_recent(
        self,
        email: str,
        *,
        window: timedelta,
        now: datetime,
    ) -> int: ...

    async def clear(self, email: str) -> None: ...

    async def purge_expired(self, now: datetime, window: timedelta) -> int: ...


@runtime_checkable
class MfaCodeRepoProtocol(Protocol):
    async def put(self, code: MfaCode) -> None: ...

    async def verify(
        self,
        *,
        user_id: str,
        kind: MfaKind,
        raw_code: str,
    ) -> MfaVerifyResult: ...

    async def delete(self, *, user_id: str, kind: MfaKind | None = None) -> None: ...

    async def find(self, *, user_id: str, kind: MfaKind) -> MfaCode | None: ...

    async def purge_expired(self, now: datetime | None = None) -> int: ...


# Re-exported here so callers don't need a deeper import. Lives in the
# Mongo backend module because it predates the SQL backend; the dataclass
# is backend-agnostic, only the import path is historical.
from regstack.backends.mongo.repositories.mfa_code_repo import MfaVerifyResult  # noqa: E402
