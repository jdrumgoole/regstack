"""Regression tests for the FrozenClock default epoch.

MongoDB's TTL monitor reaps documents whose ``expires_at`` is in the
past relative to *real wall-clock time*, on a ~60s cycle, regardless of
the Clock injected into the app. If ``FrozenClock()`` defaults to a
past instant, every TTL-indexed row a test writes (oauth_states,
pending_registrations, mfa_codes, login_attempts, blacklist) is born
already-expired, and any test whose flow straddles a TTL sweep loses
its rows mid-flight — a once-in-many-runs parallel flake.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from regstack.auth.clock import FrozenClock


def test_frozen_clock_default_is_far_in_the_real_future() -> None:
    # A year of margin keeps long-lived CI images and clock skew safe.
    assert FrozenClock().now() > datetime.now(UTC) + timedelta(days=365)


def test_frozen_clock_advance_and_set() -> None:
    clock = FrozenClock(start=datetime(2125, 6, 1, tzinfo=UTC))
    clock.advance(timedelta(seconds=30))
    assert clock.now() == datetime(2125, 6, 1, 0, 0, 30, tzinfo=UTC)
    clock.set(datetime(2125, 7, 1, tzinfo=UTC))
    assert clock.now() == datetime(2125, 7, 1, tzinfo=UTC)
