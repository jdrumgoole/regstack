from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from regstack.auth.tokens import hash_token

if TYPE_CHECKING:
    from regstack.config.schema import RegStackConfig


def generate_numeric_code(length: int) -> str:
    """Cryptographically random numeric code as a zero-padded string.

    Using ``secrets.randbelow`` (rather than ``random.randint``) keeps the
    distribution uniform without leaking via the timing of ``randint``'s
    rejection sampling — which itself uses ``getrandbits`` — but ``secrets``
    is the standard answer for this in modern Python.
    """
    if length < 1:
        raise ValueError("length must be >= 1")
    upper_exclusive = 10**length
    return f"{secrets.randbelow(upper_exclusive):0{length}d}"


def generate_mfa_code(config: RegStackConfig) -> tuple[str, str]:
    """Return ``(raw_code, code_hash)``."""
    raw = generate_numeric_code(config.sms_code_length)
    return raw, hash_token(raw)
