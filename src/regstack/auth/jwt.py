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
    """Decoded JWT claims for a regstack-issued token.

    Returned by :meth:`JwtCodec.decode` and produced as a side-effect of
    :meth:`JwtCodec.encode`. Carries everything callers need to enforce
    revocation and audit trails without re-decoding the raw token.
    """

    sub: str
    """The token subject — for session tokens, the user id."""

    jti: str
    """Per-token unique id used by the blacklist repo for explicit
    revocation on logout."""

    iat: datetime
    """When the token was issued. Tz-aware. Stored as a float (RFC 7519
    NumericDate) so the bulk-revoke comparison ``iat <= cutoff`` is
    precise even when a login completes microseconds after a password
    change."""

    exp: datetime
    """Expiry timestamp. Tz-aware."""

    purpose: str
    """Token kind — ``session``, ``password_reset``, ``email_change``,
    ``phone_setup``, or ``login_mfa``. Each kind is signed with a
    different derived key."""

    def to_claims(self, audience: str | None) -> dict[str, Any]:
        """Serialize as a JWT claims dict, including ``aud`` if set.

        Args:
            audience: Optional audience claim. When non-``None``, added
                as ``aud``; ``JwtCodec.decode`` then validates it.

        Returns:
            A dict suitable for ``pyjwt.encode``.
        """
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
    """Protocol for anything that can answer "is this `jti` revoked?".

    The :class:`~regstack.backends.protocols.BlacklistRepoProtocol`
    satisfies this contract; auth dependencies depend on it abstractly
    so tests can substitute a stub.
    """

    async def is_revoked(self, jti: str) -> bool: ...


class JwtCodec:
    """Encode and decode regstack's signed JWTs.

    Each *purpose* (``session``, ``password_reset``, ``email_change``,
    ``phone_setup``, ``login_mfa``) signs with a separate key derived
    from ``config.jwt_secret`` via HMAC-SHA256. Compromise of one
    derived key does not compromise the others, and an attacker who
    captures a session token cannot replay it as a password-reset
    token.

    Expiry (``exp``) and issued-at (``iat``) are evaluated against the
    injected :class:`~regstack.auth.clock.Clock`, not the system wall
    clock — that's the seam ``FrozenClock``-driven tests rely on.
    """

    def __init__(self, config: RegStackConfig, clock: Clock) -> None:
        """Bind the codec to a config and a clock.

        Args:
            config: The active :class:`~regstack.config.schema.RegStackConfig`.
                ``config.jwt_secret`` must be non-empty.
            clock: The clock used for issuing ``iat``/``exp`` and for
                validating expiry on decode.

        Raises:
            ValueError: If ``config.jwt_secret`` is empty. Use
                ``regstack init`` to generate one, or set
                ``REGSTACK_JWT_SECRET``.
        """
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
        """Sign and return a JWT plus its decoded payload.

        Args:
            subject: The ``sub`` claim — usually a user id.
            purpose: Logical token kind (``session`` by default). The
                derived signing key depends on this string, so a token
                minted with one purpose cannot be decoded with another.
            ttl_seconds: Override for the token lifetime. When
                ``None``, ``config.jwt_ttl_seconds`` is used.

        Returns:
            A ``(token, payload)`` tuple. ``token`` is the encoded
            string the caller hands to the client; ``payload`` is the
            in-memory :class:`TokenPayload` (useful when the caller
            also needs to record the ``jti`` for later revocation).
        """
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
        """Verify a token's signature and decode its claims.

        Verification is strict: signature, required-claims set, and
        ``aud`` (when configured) must all pass. ``exp`` is checked
        against the injected :class:`Clock`, **not** ``time.time()``,
        so frozen-clock tests stay deterministic. The ``purpose`` claim
        must match the expected purpose — a session token cannot
        satisfy ``decode(..., purpose="password_reset")``.

        Args:
            token: The encoded JWT string from the client.
            purpose: The expected token kind. Must match the value
                used at :meth:`encode` time.

        Returns:
            The decoded :class:`TokenPayload`.

        Raises:
            TokenError: On any failure — bad signature, expired token,
                purpose mismatch, missing required claim, etc.
        """
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
    """Decide whether a token has been bulk-revoked.

    Bulk revocation lets regstack invalidate every outstanding session
    when a user changes their password (or admin disables them) without
    enumerating every ``jti``. The user document carries a
    ``tokens_invalidated_after`` timestamp; any token with
    ``iat <= cutoff`` is rejected.

    The comparison is ``<=`` (not ``<``) so a token issued at exactly
    the cutoff instant is treated as before-the-change for security.
    Float-precision ``iat`` (RFC 7519 NumericDate) means a login
    completing microseconds after a password change has
    ``iat > cutoff`` and survives.

    Args:
        payload: The decoded :class:`TokenPayload`.
        cutoff: The user's ``tokens_invalidated_after`` value, or
            ``None`` if they've never been bulk-revoked.

    Returns:
        ``True`` if the token must be rejected; ``False`` if it's
        still valid (subject to the per-token blacklist check).
    """
    if cutoff is None:
        return False
    return payload.iat <= cutoff
