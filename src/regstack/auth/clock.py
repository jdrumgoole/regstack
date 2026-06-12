from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of "now" — the seam that makes time-sensitive code testable.

    JWT issuance, JWT expiry validation, lockout window calculations,
    and bulk-revoke comparisons all read time through this protocol
    rather than calling ``datetime.now()`` directly. Production passes
    :class:`SystemClock`; tests pass :class:`FrozenClock` so a single
    test can deterministically assert "this token expires in exactly
    7200 seconds" without sleeping.
    """

    def now(self) -> datetime:
        """Return the current tz-aware UTC datetime."""
        ...


class SystemClock:
    """Production :class:`Clock` — wraps ``datetime.now(UTC)``."""

    def now(self) -> datetime:
        """Return the current wall-clock time as a tz-aware UTC datetime."""
        return datetime.now(UTC)


class FrozenClock:
    """Test :class:`Clock` — returns a fixed timestamp until advanced.

    Pin the clock to a known instant for the lifetime of a test, then
    advance it explicitly to step over expiry boundaries::

        clock = FrozenClock()
        token, _ = codec.encode("user-1")
        clock.advance(timedelta(seconds=7201))   # past exp
        with pytest.raises(TokenError):
            codec.decode(token)
    """

    def __init__(self, start: datetime | None = None) -> None:
        """Pin the clock at ``start`` (default 2125-01-01 UTC).

        The default is deliberately ~100 years in the *future*. MongoDB's
        TTL monitor deletes documents by comparing their ``expires_at``
        against real wall-clock time on a ~60s cycle — it knows nothing
        about this injected clock. A frozen "now" in the past would give
        every TTL-indexed test row (oauth_states, pending_registrations,
        mfa_codes, login_attempts, blacklist) an already-elapsed
        ``expires_at``, and any test straddling a TTL sweep would have
        its rows reaped mid-flight. A far-future pin keeps the reaper
        permanently out of reach while staying deterministic.

        Args:
            start: The initial timestamp. Should be tz-aware. Defaults
                to ``2125-01-01T00:00:00Z``.
        """
        self._now = start or datetime(2125, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the currently-pinned timestamp."""
        return self._now

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward by ``delta``.

        Args:
            delta: How far to advance. Negative values are accepted but
                rarely useful.
        """
        self._now += delta

    def set(self, when: datetime) -> None:
        """Reset the clock to an absolute instant.

        Args:
            when: The new "now". Should be tz-aware.
        """
        self._now = when
