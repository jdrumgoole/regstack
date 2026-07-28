from __future__ import annotations

import hashlib
import hmac
import secrets

MIN_JWT_SECRET_LENGTH = 32
"""Shortest master JWT secret regstack will accept.

HMAC-SHA256 zero-pads a key shorter than its block size rather than
rejecting it, so a one-character secret signs every token with a handful
of bits of entropy and no error. Enforced at ``JwtCodec`` construction
and reported by ``regstack doctor``.
"""


def derive_secret(master: str | bytes, purpose: str) -> bytes:
    """Derive a purpose-specific secret from the master JWT secret.

    Uses HMAC-SHA256 so every subsystem (verification tokens, password reset
    tokens, refresh tokens, etc.) signs with a different key. Compromising one
    derived key does not compromise the master.
    """
    if isinstance(master, str):
        master = master.encode("utf-8")
    return hmac.new(master, purpose.encode("utf-8"), hashlib.sha256).digest()


def generate_secret(num_bytes: int = 64) -> str:
    """Return a URL-safe random secret suitable for the JWT master key."""
    return secrets.token_urlsafe(num_bytes)
