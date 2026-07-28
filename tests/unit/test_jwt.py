from __future__ import annotations

import secrets
from datetime import timedelta

import pytest

from regstack.auth.clock import FrozenClock
from regstack.auth.jwt import JwtCodec, TokenError, is_payload_bulk_revoked
from regstack.config.schema import RegStackConfig
from regstack.config.secrets import MIN_JWT_SECRET_LENGTH


def _make_codec(ttl: int = 7200) -> tuple[JwtCodec, FrozenClock]:
    config = RegStackConfig(
        jwt_secret=secrets.token_urlsafe(32),
        jwt_ttl_seconds=ttl,
    )
    clock = FrozenClock()
    return JwtCodec(config, clock), clock


def test_encode_decode_round_trip() -> None:
    codec, _ = _make_codec()
    token, payload = codec.encode("user-123")
    decoded = codec.decode(token)
    assert decoded.sub == "user-123"
    assert decoded.jti == payload.jti
    assert decoded.purpose == "session"


def test_expired_token_rejected() -> None:
    codec, clock = _make_codec(ttl=60)
    token, _ = codec.encode("user-1")
    clock.advance(timedelta(seconds=120))
    with pytest.raises(TokenError):
        codec.decode(token)


def test_purpose_separation() -> None:
    codec, _ = _make_codec()
    token, _ = codec.encode("user-1", purpose="verification")
    # decoding under the wrong purpose fails (different signing key)
    with pytest.raises(TokenError):
        codec.decode(token)  # default purpose is 'session'


def test_bulk_revocation_predicate() -> None:
    codec, clock = _make_codec()
    _, payload = codec.encode("user-1")
    cutoff_before = clock.now() - timedelta(seconds=10)
    cutoff_after = clock.now() + timedelta(seconds=10)
    assert not is_payload_bulk_revoked(payload, cutoff_before)
    assert is_payload_bulk_revoked(payload, cutoff_after)
    # Equal-instant case: cutoff coincides with iat → conservative revoke.
    assert is_payload_bulk_revoked(payload, payload.iat)


def test_empty_secret_rejected() -> None:
    config = RegStackConfig()  # default jwt_secret is empty
    with pytest.raises(ValueError, match="jwt_secret is empty"):
        JwtCodec(config, FrozenClock())


@pytest.mark.parametrize("length", [1, 8, MIN_JWT_SECRET_LENGTH - 1])
def test_short_secret_rejected(length: int) -> None:
    config = RegStackConfig(jwt_secret="x" * length)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="jwt_secret is too short"):
        JwtCodec(config, FrozenClock())


def test_secret_at_minimum_length_accepted() -> None:
    config = RegStackConfig(jwt_secret="x" * MIN_JWT_SECRET_LENGTH)  # type: ignore[arg-type]
    codec = JwtCodec(config, FrozenClock())
    token, payload = codec.encode("user-1")
    assert codec.decode(token).jti == payload.jti
