from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regstack.auth.clock import Clock
    from regstack.config.schema import RegStackConfig
    from regstack.backends.mongo.repositories.login_attempt_repo import LoginAttemptRepo


@dataclass(slots=True, frozen=True)
class LockoutDecision:
    locked: bool
    retry_after_seconds: int


class LockoutService:
    """Counts failed logins per email in a sliding window. Locks the account
    when the threshold is exceeded for the rest of the window.

    Disabled (always returns ``locked=False``) when ``config.rate_limit_disabled``
    is set — tests rely on this to avoid timing flakes.
    """

    def __init__(
        self,
        *,
        attempts: LoginAttemptRepo,
        config: RegStackConfig,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._config = config
        self._clock = clock

    @property
    def _window(self) -> timedelta:
        return timedelta(seconds=self._config.login_lockout_window_seconds)

    async def check(self, email: str) -> LockoutDecision:
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
        if self._config.rate_limit_disabled:
            return
        await self._attempts.record_failure(email, when=self._clock.now(), ip=ip)

    async def clear(self, email: str) -> None:
        await self._attempts.clear(email)
