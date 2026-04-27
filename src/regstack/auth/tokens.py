from __future__ import annotations

import hashlib
import secrets


def generate_verification_token() -> tuple[str, str]:
    """Return (raw_token_for_email, hash_for_db) for a single-use verification link.

    The raw token is only ever sent in the verification email; only the
    SHA-256 digest hits the database, so a database read does not yield
    usable tokens.
    """
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
