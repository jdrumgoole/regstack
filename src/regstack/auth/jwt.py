from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import jwt as pyjwt

from regstack.config.secrets import derive_secret

if TYPE_CHECKING:
    from regstack.auth.clock import Clock
    from regstack.config.schema import RegStackConfig

_DEFAULT_PURPOSE = "session"


class TokenError(Exception):
    """Raised when a token cannot be decoded or is no longer valid."""


@dataclass(slots=True)
class TokenPayload:
    sub: str
    jti: str
    iat: datetime
    exp: datetime
    purpose: str

    def to_claims(self, audience: str | None) -> dict[str, Any]:
        # iat is emitted as a float so a within-the-same-second login after a
        # bulk-revoke cutoff isn't falsely rejected. RFC 7519 NumericDate
        # explicitly allows non-integer values.
        claims: dict[str, Any] = {
            "sub": self.sub,
            "jti": self.jti,
            "iat": self.iat.timestamp(),
            "exp": int(self.exp.timestamp()),
            "purpose": self.purpose,
        }
        if audience is not None:
            claims["aud"] = audience
        return claims


class RevocationChecker(Protocol):
    """Anything that knows whether a `jti` has been revoked."""

    async def is_revoked(self, jti: str) -> bool: ...


class JwtCodec:
    """Encode and decode regstack's session JWTs.

    Each purpose (session, verification, password_reset) signs with a key
    derived from ``config.jwt_secret`` so a leak of one derived key does
    not compromise the master.
    """

    def __init__(self, config: RegStackConfig, clock: Clock) -> None:
        if not config.jwt_secret.get_secret_value():
            raise ValueError(
                "RegStackConfig.jwt_secret is empty. Run `regstack init` to generate one, "
                "or set REGSTACK_JWT_SECRET."
            )
        self._config = config
        self._clock = clock

    def _key(self, purpose: str) -> bytes:
        return derive_secret(self._config.jwt_secret.get_secret_value(), purpose)

    def encode(
        self,
        subject: str,
        *,
        purpose: str = _DEFAULT_PURPOSE,
        ttl_seconds: int | None = None,
    ) -> tuple[str, TokenPayload]:
        now = self._clock.now()
        ttl = ttl_seconds if ttl_seconds is not None else self._config.jwt_ttl_seconds
        exp = now + timedelta(seconds=ttl)
        payload = TokenPayload(
            sub=subject,
            jti=secrets.token_urlsafe(16),
            iat=now,
            exp=exp,
            purpose=purpose,
        )
        token = pyjwt.encode(
            payload.to_claims(self._config.jwt_audience),
            self._key(purpose),
            algorithm=self._config.jwt_algorithm,
        )
        return token, payload

    def decode(self, token: str, *, purpose: str = _DEFAULT_PURPOSE) -> TokenPayload:
        try:
            # We disable pyjwt's exp/iat checks because they use the system
            # wall clock; we re-check both against the injected Clock so that
            # FrozenClock-driven tests (and any future time-mocking host) get
            # consistent behaviour.
            claims = pyjwt.decode(
                token,
                self._key(purpose),
                algorithms=[self._config.jwt_algorithm],
                audience=self._config.jwt_audience,
                options={
                    "require": ["sub", "exp", "iat", "jti", "purpose"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except pyjwt.PyJWTError as exc:
            raise TokenError(str(exc)) from exc

        if claims.get("purpose") != purpose:
            raise TokenError("token purpose mismatch")

        now = self._clock.now()
        tz = now.tzinfo
        iat = datetime.fromtimestamp(float(claims["iat"]), tz=tz)
        exp = datetime.fromtimestamp(int(claims["exp"]), tz=tz)
        if now >= exp:
            raise TokenError("Signature has expired")

        return TokenPayload(
            sub=str(claims["sub"]),
            jti=str(claims["jti"]),
            iat=iat,
            exp=exp,
            purpose=str(claims["purpose"]),
        )


def is_payload_bulk_revoked(payload: TokenPayload, cutoff: datetime | None) -> bool:
    """Return True when the user's bulk-revocation cutoff is at-or-after the
    token's ``iat``. Tokens issued strictly after the cutoff (even by
    microseconds) survive; tokens issued at exactly the same instant are
    treated as before-the-change for security.
    """
    if cutoff is None:
        return False
    return payload.iat <= cutoff
