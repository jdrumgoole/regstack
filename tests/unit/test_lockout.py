from __future__ import annotations

import secrets
from datetime import timedelta

import pytest
import pytest_asyncio

from regstack.auth.clock import FrozenClock
from regstack.auth.lockout import LockoutService
from regstack.config.schema import RegStackConfig
from regstack.backends.mongo.repositories.login_attempt_repo import LoginAttemptRepo


@pytest.fixture
def backend_kind() -> str:
    """LockoutService unit tests pin to Mongo's repo so the fixture
    parametrization in the top-level conftest doesn't double the
    matrix; cross-backend lockout coverage lives in
    tests/integration/test_login_lockout.py.
    """
    return "mongo"


@pytest_asyncio.fixture
async def attempts_repo(mongo_client):
    """Fresh per-test attempts collection inside the legacy mongo_client
    DB. Pins to a unique collection name so leaks from other tests can't
    skew counts.
    """
    coll_name = f"login_attempts_{secrets.token_hex(4)}"
    db = mongo_client.get_default_database()
    yield LoginAttemptRepo(db, coll_name)
    await db[coll_name].drop()


def _make_config(*, threshold: int = 3, window: int = 60) -> RegStackConfig:
    return RegStackConfig(
        jwt_secret=secrets.token_urlsafe(32),
        rate_limit_disabled=False,
        login_lockout_threshold=threshold,
        login_lockout_window_seconds=window,
    )


@pytest.mark.asyncio
async def test_locks_after_threshold_failures(attempts_repo: LoginAttemptRepo) -> None:
    clock = FrozenClock()
    service = LockoutService(attempts=attempts_repo, config=_make_config(threshold=3), clock=clock)
    email = "alice@example.com"
    for _ in range(2):
        await service.record_failure(email)
    assert (await service.check(email)).locked is False

    await service.record_failure(email)
    decision = await service.check(email)
    assert decision.locked is True
    assert decision.retry_after_seconds == 60


@pytest.mark.asyncio
async def test_window_advances_unlock(attempts_repo: LoginAttemptRepo) -> None:
    clock = FrozenClock()
    service = LockoutService(
        attempts=attempts_repo, config=_make_config(threshold=2, window=60), clock=clock
    )
    email = "bob@example.com"
    await service.record_failure(email)
    await service.record_failure(email)
    assert (await service.check(email)).locked is True

    # Advance past the window — count_recent only sees attempts since cutoff.
    clock.advance(timedelta(seconds=61))
    assert (await service.check(email)).locked is False


@pytest.mark.asyncio
async def test_clear_resets_counter(attempts_repo: LoginAttemptRepo) -> None:
    clock = FrozenClock()
    service = LockoutService(attempts=attempts_repo, config=_make_config(threshold=2), clock=clock)
    email = "carol@example.com"
    await service.record_failure(email)
    await service.record_failure(email)
    assert (await service.check(email)).locked is True

    await service.clear(email)
    assert (await service.check(email)).locked is False


@pytest.mark.asyncio
async def test_disabled_when_rate_limit_disabled(attempts_repo: LoginAttemptRepo) -> None:
    clock = FrozenClock()
    config = RegStackConfig(
        jwt_secret=secrets.token_urlsafe(32),
        rate_limit_disabled=True,
        login_lockout_threshold=1,
    )
    service = LockoutService(attempts=attempts_repo, config=config, clock=clock)
    # record_failure should be a no-op too — no docs inserted.
    await service.record_failure("dave@example.com")
    assert (await service.check("dave@example.com")).locked is False
