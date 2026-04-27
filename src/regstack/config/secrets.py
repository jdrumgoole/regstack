from __future__ import annotations

import hashlib
import hmac
import secrets


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
