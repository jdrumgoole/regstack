from __future__ import annotations

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher


class PasswordHasher:
    """Thin wrapper over ``pwdlib`` so we can swap algorithms without touching callers."""

    def __init__(self) -> None:
        self._hasher = PasswordHash((Argon2Hasher(),))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        return self._hasher.verify(password, hashed)

    def needs_rehash(self, hashed: str) -> bool:
        return self._hasher.check_needs_rehash(hashed)
