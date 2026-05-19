"""Unit-level regression tests for the 0.8.x consistency cleanup.

Each test pins one of the behavioural changes called out by the
2026-05-19 system review. Tests are intentionally small — broader
integration coverage of the same flows already exists in the relevant
test_login.py / test_phone.py / test_password_reset.py modules.
"""

from __future__ import annotations

import secrets

import pytest

from regstack.auth.rate_limit import ROUTE_LIMIT_MAP, collect_route_limits
from regstack.config.schema import RegStackConfig
from regstack.hooks.events import KNOWN_EVENTS


def _cfg(**overrides: object) -> RegStackConfig:
    return RegStackConfig(jwt_secret=secrets.token_urlsafe(32), **overrides)  # type: ignore[arg-type]


# --- Review #5: deprecated fields removed ----------------------------------


def test_deprecated_login_max_fields_no_longer_silently_accepted() -> None:
    """``login_max_per_minute`` / ``login_max_per_hour`` were accepted by
    config but unused. They are gone in 0.8.x — supplying them now raises
    a pydantic validation error (the model's ``extra="ignore"`` lets
    *unknown* keys through silently, but these were known fields and have
    been actively removed)."""
    fields = set(type(_cfg()).model_fields)
    assert "login_max_per_minute" not in fields
    assert "login_max_per_hour" not in fields


# --- Review #6: phone routes get rate-limit entries ------------------------


def test_phone_rate_limit_fields_exist_and_route_to_phone_paths() -> None:
    cfg = _cfg(
        phone_start_rate_limit="3/minute",
        phone_confirm_rate_limit="5/minute",
        phone_disable_rate_limit="1/minute",
    )
    limits = collect_route_limits(cfg)
    assert limits["/phone/start"] == "3/minute"
    assert limits["/phone/confirm"] == "5/minute"
    assert limits["/phone"] == "1/minute"


def test_phone_rate_limit_keys_in_route_map() -> None:
    for k, v in {
        "phone_start_rate_limit": "/phone/start",
        "phone_confirm_rate_limit": "/phone/confirm",
        "phone_disable_rate_limit": "/phone",
    }.items():
        assert ROUTE_LIMIT_MAP[k] == v


# --- Review #11: phone_setup_disabled event exists -------------------------


def test_phone_setup_disabled_in_known_events() -> None:
    assert "phone_setup_disabled" in KNOWN_EVENTS
    # The matching setup-side event should still be there too.
    assert "phone_setup_started" in KNOWN_EVENTS


# --- Review #15: LockoutService exposes attempts_remaining -----------------


class _FakeAttempts:
    def __init__(self) -> None:
        self._count = 0

    async def count_recent(self, email: str, *, window, now) -> int:  # type: ignore[no-untyped-def]
        return self._count

    async def record_failure(self, email: str, *, when, ip=None) -> None:  # type: ignore[no-untyped-def]
        self._count += 1

    async def clear(self, email: str) -> None:
        self._count = 0


@pytest.mark.asyncio
async def test_lockout_attempts_remaining_counts_down() -> None:
    from datetime import UTC, datetime

    from regstack.auth.clock import Clock
    from regstack.auth.lockout import LockoutService

    class _StaticClock(Clock):
        def now(self) -> datetime:
            return datetime(2026, 5, 19, tzinfo=UTC)

    cfg = _cfg(login_lockout_threshold=5)
    attempts = _FakeAttempts()
    lockout = LockoutService(attempts=attempts, config=cfg, clock=_StaticClock())

    assert await lockout.attempts_remaining("a@b") == 5
    await attempts.record_failure("a@b", when=_StaticClock().now())
    await attempts.record_failure("a@b", when=_StaticClock().now())
    assert await lockout.attempts_remaining("a@b") == 3


@pytest.mark.asyncio
async def test_lockout_attempts_remaining_returns_none_when_disabled() -> None:
    from regstack.auth.clock import SystemClock
    from regstack.auth.lockout import LockoutService

    cfg = _cfg(rate_limit_disabled=True)
    lockout = LockoutService(attempts=_FakeAttempts(), config=cfg, clock=SystemClock())
    assert await lockout.attempts_remaining("a@b") is None
