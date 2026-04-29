"""Storage-layer protocols.

Each backend (`mongo`, `sql`) provides one concrete implementation per
protocol. Routers and services depend only on these protocols, so a
backend swap is a wiring change, not a code change.

Mongo's existing semantics are the contract: anything new (SQLAlchemy,
in-memory, etc.) is judged by behavioural parity, not by whether the SQL
or Mongo idiom feels "right" to the implementer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from regstack.models.mfa_code import MfaCode, MfaKind
from regstack.models.oauth_identity import OAuthIdentity
from regstack.models.oauth_state import OAuthState
from regstack.models.pending_registration import PendingRegistration
from regstack.models.user import BaseUser


class MfaVerifyOutcome(StrEnum):
    """Result of submitting an SMS MFA code to the repo.

    The five possible values:

    - ``OK`` — the code matched and the row was consumed.
    - ``WRONG`` — the code didn't match. ``attempts_remaining`` on
      the paired :class:`MfaVerifyResult` says how many tries are
      left before the row is deleted (forcing a re-issue).
    - ``EXPIRED`` — a row exists but its TTL has passed.
    - ``LOCKED`` — too many wrong guesses; the row was deleted and
      the user must request a new code.
    - ``MISSING`` — no outstanding code for this user / kind.
    """

    OK = "ok"
    WRONG = "wrong"
    EXPIRED = "expired"
    LOCKED = "locked"
    MISSING = "missing"


@dataclass(slots=True, frozen=True)
class MfaVerifyResult:
    """Outcome of :meth:`MfaCodeRepoProtocol.verify`."""

    outcome: MfaVerifyOutcome
    """Which terminal state the verify call landed in. See
    :class:`MfaVerifyOutcome`."""

    attempts_remaining: int = 0
    """For :attr:`MfaVerifyOutcome.WRONG`, how many more guesses the
    user has before the code is deleted and they must request a new
    one. ``0`` for any other outcome."""


class UserAlreadyExistsError(Exception):
    """Raised when an insert / email-change collides with an existing user.

    Backend-agnostic — every repo raises this same type on its
    integrity-error path so callers can branch on the type without
    importing a backend module. Surfaced by the registration and
    change-email routers as HTTP 409.
    """


class PendingAlreadyExistsError(Exception):
    """A pending registration with this email already exists.

    In practice the registration router uses an upsert so this
    exception is rarely raised — kept as the backend-agnostic name
    for the error so future callers don't need to import a backend
    module.
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

    async def count_unexpired(self, now: datetime | None = None) -> int:
        """Count pending-registration rows whose ``expires_at`` is in the future.

        "Unexpired" rather than a raw row-count because SQL backends
        accumulate dead rows until ``purge_expired`` runs — a raw
        count would double-report a verification email that's been
        unanswered for a month and a fresh one sent today.

        Args:
            now: Reference instant. Defaults to ``datetime.now(UTC)``.

        Returns:
            Number of pending rows with ``expires_at > now``.
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


class OAuthIdentityAlreadyLinkedError(Exception):
    """An identity is already linked to a regstack user.

    Raised by :meth:`OAuthIdentityRepoProtocol.create` when the
    ``UNIQUE(provider, subject_id)`` or ``UNIQUE(user_id, provider)``
    constraint fires. Routers translate this to HTTP 409.
    """


@runtime_checkable
class OAuthIdentityRepoProtocol(Protocol):
    """External-OAuth identities linked to regstack users.

    One row per ``(provider, subject_id)``. Two unique constraints —
    see :class:`~regstack.models.oauth_identity.OAuthIdentity` for
    the rationale.
    """

    async def create(self, identity: OAuthIdentity) -> OAuthIdentity:
        """Insert a new identity. Raises :class:`OAuthIdentityAlreadyLinkedError`
        on either unique-constraint violation.
        """
        ...

    async def find_by_subject(self, *, provider: str, subject_id: str) -> OAuthIdentity | None: ...

    async def list_for_user(self, user_id: str) -> list[OAuthIdentity]:
        """Every identity linked to ``user_id``, sorted by ``linked_at`` ascending."""
        ...

    async def delete(self, *, user_id: str, provider: str) -> bool:
        """Delete one identity. Returns True if a row was removed."""
        ...

    async def delete_by_user_id(self, user_id: str) -> int:
        """Delete every identity for a user. Called from the
        delete-account path so identities don't outlive their owner.
        """
        ...

    async def touch_last_used(self, *, provider: str, subject_id: str, when: datetime) -> None:
        """Update ``last_used_at``. Called on each successful sign-in
        through this identity. Best-effort — failure is logged, not
        raised.
        """
        ...


@runtime_checkable
class OAuthStateRepoProtocol(Protocol):
    """Server-side state rows for in-flight OAuth flows.

    The OAuth ``state`` parameter the browser carries is just the
    row's ``id``. The PKCE ``code_verifier`` and the post-callback
    ``result_token`` are server-side fields on the row.
    """

    async def create(self, state: OAuthState) -> None:
        """Insert. Caller picks the row id (usually
        :func:`secrets.token_urlsafe`).
        """
        ...

    async def find(self, state_id: str) -> OAuthState | None: ...

    async def set_result_token(self, state_id: str, token: str) -> None:
        """Stash the session JWT after a successful callback so the
        SPA can pick it up via :meth:`consume`.
        """
        ...

    async def consume(self, state_id: str) -> OAuthState | None:
        """Atomic read + delete. The exchange endpoint reads the
        ``result_token``; the row is gone after this call returns,
        making the exchange single-use.

        Returns ``None`` if the row is missing.
        """
        ...

    async def purge_expired(self, now: datetime | None = None) -> int:
        """Sweep expired rows. Mongo has a TTL index; SQL relies on
        a periodic call to this.
        """
        ...
