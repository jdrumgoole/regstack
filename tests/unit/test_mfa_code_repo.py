from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from regstack.auth.clock import FrozenClock
from regstack.auth.tokens import hash_token
from regstack.backends.mongo.repositories.mfa_code_repo import MfaCodeRepo, MfaVerifyOutcome
from regstack.models.mfa_code import MfaCode


@pytest_asyncio.fixture
async def repo(mongo_client, config):
    db = mongo_client[config.mongodb_database]
    return MfaCodeRepo(db, "mfa_codes_test", clock=FrozenClock())


def _put_code(clock: FrozenClock, *, code: str, max_attempts: int = 5) -> MfaCode:
    return MfaCode(
        user_id="u1",
        kind="login_mfa",
        code_hash=hash_token(code),
        expires_at=clock.now() + timedelta(seconds=60),
        max_attempts=max_attempts,
    )


@pytest.mark.asyncio
async def test_verify_correct_code(repo: MfaCodeRepo) -> None:
    clock = repo._clock  # type: ignore[attr-defined]
    await repo.put(_put_code(clock, code="123456"))
    result = await repo.verify(user_id="u1", kind="login_mfa", raw_code="123456")
    assert result.outcome is MfaVerifyOutcome.OK
    # Code is consumed — second verify is missing.
    again = await repo.verify(user_id="u1", kind="login_mfa", raw_code="123456")
    assert again.outcome is MfaVerifyOutcome.MISSING


@pytest.mark.asyncio
async def test_wrong_code_increments_attempts(repo: MfaCodeRepo) -> None:
    clock = repo._clock  # type: ignore[attr-defined]
    await repo.put(_put_code(clock, code="111111", max_attempts=3))
    result = await repo.verify(user_id="u1", kind="login_mfa", raw_code="999999")
    assert result.outcome is MfaVerifyOutcome.WRONG
    assert result.attempts_remaining == 2

    result = await repo.verify(user_id="u1", kind="login_mfa", raw_code="999999")
    assert result.outcome is MfaVerifyOutcome.WRONG
    assert result.attempts_remaining == 1


@pytest.mark.asyncio
async def test_max_attempts_locks_and_deletes(repo: MfaCodeRepo) -> None:
    clock = repo._clock  # type: ignore[attr-defined]
    await repo.put(_put_code(clock, code="111111", max_attempts=2))
    await repo.verify(user_id="u1", kind="login_mfa", raw_code="000000")
    final = await repo.verify(user_id="u1", kind="login_mfa", raw_code="000000")
    assert final.outcome is MfaVerifyOutcome.LOCKED
    # Subsequent attempt finds nothing.
    again = await repo.verify(user_id="u1", kind="login_mfa", raw_code="111111")
    assert again.outcome is MfaVerifyOutcome.MISSING


@pytest.mark.asyncio
async def test_expired_code(repo: MfaCodeRepo) -> None:
    clock = repo._clock  # type: ignore[attr-defined]
    await repo.put(_put_code(clock, code="123456"))
    clock.advance(timedelta(seconds=120))
    result = await repo.verify(user_id="u1", kind="login_mfa", raw_code="123456")
    assert result.outcome is MfaVerifyOutcome.EXPIRED


@pytest.mark.asyncio
async def test_put_overwrites_previous(repo: MfaCodeRepo) -> None:
    clock = repo._clock  # type: ignore[attr-defined]
    await repo.put(_put_code(clock, code="111111"))
    await repo.put(_put_code(clock, code="222222"))
    # Old code rejected, new accepted.
    bad = await repo.verify(user_id="u1", kind="login_mfa", raw_code="111111")
    assert bad.outcome is MfaVerifyOutcome.WRONG
    good = await repo.verify(user_id="u1", kind="login_mfa", raw_code="222222")
    assert good.outcome is MfaVerifyOutcome.OK


@pytest.mark.asyncio
async def test_delete_clears_all_kinds(repo: MfaCodeRepo) -> None:
    clock = repo._clock  # type: ignore[attr-defined]
    await repo.put(
        MfaCode(
            user_id="u1",
            kind="login_mfa",
            code_hash=hash_token("a"),
            expires_at=clock.now() + timedelta(seconds=60),
        )
    )
    await repo.put(
        MfaCode(
            user_id="u1",
            kind="phone_setup",
            code_hash=hash_token("b"),
            expires_at=clock.now() + timedelta(seconds=60),
        )
    )
    await repo.delete(user_id="u1")
    assert (
        await repo.verify(user_id="u1", kind="login_mfa", raw_code="a")
    ).outcome is MfaVerifyOutcome.MISSING
    assert (
        await repo.verify(user_id="u1", kind="phone_setup", raw_code="b")
    ).outcome is MfaVerifyOutcome.MISSING
