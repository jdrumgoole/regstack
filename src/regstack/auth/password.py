from __future__ import annotations

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher


class PasswordHasher:
    """Argon2id password hashing facade.

    Thin wrapper over ``pwdlib`` so the algorithm choice (and any
    future algorithm rotation) lives behind one interface. Callers
    don't import ``pwdlib`` directly.

    A single instance is held on the :class:`~regstack.app.RegStack`
    façade as ``regstack.password_hasher``; constructing your own is
    rarely necessary.
    """

    def __init__(self) -> None:
        """Build a hasher pinned to Argon2id with library defaults."""
        self._hasher = PasswordHash((Argon2Hasher(),))

    def hash(self, password: str) -> str:
        """Hash a plaintext password with Argon2id.

        Args:
            password: The plaintext password to hash.

        Returns:
            The Argon2 PHC-formatted hash string. Includes algorithm,
            parameters, salt, and digest, so :meth:`verify` and
            :meth:`needs_rehash` can recover everything they need.
        """
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        """Constant-time check that ``password`` matches ``hashed``.

        Args:
            password: The plaintext password supplied by the user.
            hashed: A previously stored :meth:`hash` result.

        Returns:
            ``True`` if the password matches; ``False`` otherwise. No
            exception is raised on mismatch.
        """
        return self._hasher.verify(password, hashed)

    def needs_rehash(self, hashed: str) -> bool:
        """Whether a stored hash should be re-computed with newer params.

        When Argon2id parameters change (more memory, more iterations,
        different parallelism), existing hashes are still valid but
        weaker than newly-issued ones. After a successful login,
        re-hash the user's password if this returns ``True`` so
        existing accounts upgrade silently.

        Args:
            hashed: A previously stored :meth:`hash` result.

        Returns:
            ``True`` if the parameters baked into ``hashed`` differ
            from the hasher's current defaults.
        """
        return self._hasher.check_needs_rehash(hashed)
