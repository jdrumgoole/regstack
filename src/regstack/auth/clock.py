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
        """Pin the clock at ``start`` (default 2025-01-01 UTC).

        Args:
            start: The initial timestamp. Should be tz-aware. Defaults
                to ``2025-01-01T00:00:00Z`` so test datetimes are
                memorable.
        """
        self._now = start or datetime(2025, 1, 1, tzinfo=UTC)

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
