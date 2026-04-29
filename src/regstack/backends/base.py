"""Backend ABC.

A `Backend` owns the persistence story for one regstack instance: it
hands out the five repository implementations, knows how to install /
migrate its own schema, and how to tear down its connection pool when
the host shuts down. Routers and services never see the backend object
directly — they pull repos off the ``RegStack`` façade, which in turn
holds one backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from regstack.auth.clock import Clock
    from regstack.backends.protocols import (
        BlacklistRepoProtocol,
        LoginAttemptRepoProtocol,
        MfaCodeRepoProtocol,
        OAuthIdentityRepoProtocol,
        OAuthStateRepoProtocol,
        PendingRepoProtocol,
        UserRepoProtocol,
    )
    from regstack.config.schema import RegStackConfig


class BackendKind(StrEnum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    MONGO = "mongo"


class Backend(ABC):
    """A configured persistence backend for one regstack instance."""

    kind: BackendKind

    def __init__(self, *, config: RegStackConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock

    # --- Repositories ----------------------------------------------------
    # Each backend exposes the five repos as attributes after construction.
    # They are typed as protocols (not concrete classes) so consumers
    # cannot reach for backend-specific helpers by accident.

    users: UserRepoProtocol
    pending: PendingRepoProtocol
    blacklist: BlacklistRepoProtocol
    attempts: LoginAttemptRepoProtocol
    mfa_codes: MfaCodeRepoProtocol
    oauth_identities: OAuthIdentityRepoProtocol
    oauth_states: OAuthStateRepoProtocol

    # --- Lifecycle -------------------------------------------------------

    @abstractmethod
    async def install_schema(self) -> None:
        """Create indexes (Mongo) or run migrations (SQL).

        Idempotent — safe to call on every app start. Hosts typically
        invoke this from a FastAPI ``lifespan`` startup hook.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Close the underlying connection pool / client."""

    # --- Diagnostics -----------------------------------------------------

    @abstractmethod
    async def ping(self) -> None:
        """Cheap connectivity probe. Raises on failure. Used by `regstack doctor`."""
