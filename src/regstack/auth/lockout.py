from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regstack.auth.clock import Clock
    from regstack.backends.protocols import LoginAttemptRepoProtocol
    from regstack.config.schema import RegStackConfig


@dataclass(slots=True, frozen=True)
class LockoutDecision:
    """Result of a :meth:`LockoutService.check` call."""

    locked: bool
    """``True`` if the email is locked out and the login request should
    be rejected without verifying the password."""

    retry_after_seconds: int
    """How long the client should wait before retrying. Surfaced to the
    client as the HTTP ``Retry-After`` header on a 429 response. ``0``
    when ``locked=False``."""


class LockoutService:
    """Per-account login lockout — sliding window over recent failures.

    Counts failed logins per email in a window of
    ``config.login_lockout_window_seconds``. Once the count exceeds
    ``config.login_lockout_threshold``, every login attempt for that
    email is rejected with HTTP 429 (and a ``Retry-After`` header) for
    the rest of the window — even when the password is correct, so an
    attacker can't tell whether their guess was right.

    Successful logins call :meth:`clear` to wipe the recorded
    failures eagerly.

    Disabled (always returns ``locked=False`` and never writes
    failures) when ``config.rate_limit_disabled`` is set. Tests rely on
    this to avoid timing flakes.
    """

    def __init__(
        self,
        *,
        attempts: LoginAttemptRepoProtocol,
        config: RegStackConfig,
        clock: Clock,
    ) -> None:
        """Bind the service to a repo, a config, and a clock.

        Args:
            attempts: The
                :class:`~regstack.backends.protocols.LoginAttemptRepoProtocol`
                that stores failure rows.
            config: Carries the threshold and window settings, plus
                the ``rate_limit_disabled`` short-circuit.
            clock: Used for the window calculation and for stamping
                new failures with ``when=now``.
        """
        self._attempts = attempts
        self._config = config
        self._clock = clock

    @property
    def _window(self) -> timedelta:
        return timedelta(seconds=self._config.login_lockout_window_seconds)

    async def check(self, email: str) -> LockoutDecision:
        """Decide whether ``email`` is currently locked out.

        Should be called *before* the password is verified — the whole
        point is that a locked-out attacker can't probe whether their
        guess was correct by observing different responses.

        Args:
            email: The email being attempted.

        Returns:
            A :class:`LockoutDecision`. When ``locked`` is ``True``
            the caller should respond with HTTP 429 and the
            ``Retry-After`` header.
        """
        if self._config.rate_limit_disabled:
            return LockoutDecision(locked=False, retry_after_seconds=0)
        count = await self._attempts.count_recent(email, window=self._window, now=self._clock.now())
        if count >= self._config.login_lockout_threshold:
            return LockoutDecision(
                locked=True,
                retry_after_seconds=self._config.login_lockout_window_seconds,
            )
        return LockoutDecision(locked=False, retry_after_seconds=0)

    async def record_failure(self, email: str, *, ip: str | None = None) -> None:
        """Record one failed login. No-op when rate limiting is disabled.

        Args:
            email: The email that failed.
            ip: Optional source IP. Recorded for auditing; not used by
                the threshold calculation today.
        """
        if self._config.rate_limit_disabled:
            return
        await self._attempts.record_failure(email, when=self._clock.now(), ip=ip)

    async def clear(self, email: str) -> None:
        """Wipe accumulated failures for ``email``.

        Called on successful login (so the user's next mistype doesn't
        get them halfway to lockout) and on successful password reset
        (so the legitimate user isn't still gated out by the attacker's
        attempts).

        Args:
            email: The email whose failure rows should be deleted.
        """
        await self._attempts.clear(email)
