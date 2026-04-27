"""Pick the right backend implementation from ``config.database_url``.

URL scheme decides which package is loaded. SQL backends are imported
lazily so a Mongo-only deployment doesn't pay the SQLAlchemy import
cost (and vice versa).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.backends.base import Backend, BackendKind

if TYPE_CHECKING:
    from regstack.auth.clock import Clock
    from regstack.config.schema import RegStackConfig


def detect_backend_kind(database_url: str) -> BackendKind:
    """Map a ``database_url`` to a backend kind."""
    lowered = database_url.lower()
    if lowered.startswith(("mongodb://", "mongodb+srv://")):
        return BackendKind.MONGO
    if lowered.startswith(("postgresql://", "postgresql+asyncpg://", "postgres://")):
        return BackendKind.POSTGRES
    if lowered.startswith(("sqlite://", "sqlite+aiosqlite://")):
        return BackendKind.SQLITE
    raise ValueError(
        f"Unrecognised database_url scheme: {database_url!r}. "
        "Expected one of: sqlite+aiosqlite://, postgresql+asyncpg://, mongodb://."
    )


def build_backend(config: RegStackConfig, *, clock: Clock | None = None) -> Backend:
    """Construct the configured backend without opening any sockets yet
    (pools are lazy in every implementation).
    """
    from regstack.auth.clock import SystemClock

    clock_obj = clock or SystemClock()
    kind = detect_backend_kind(config.database_url.get_secret_value())
    if kind is BackendKind.MONGO:
        from regstack.backends.mongo.backend import MongoBackend

        return MongoBackend(config=config, clock=clock_obj)
    if kind in (BackendKind.SQLITE, BackendKind.POSTGRES):
        from regstack.backends.sql.backend import SqlBackend

        return SqlBackend(config=config, clock=clock_obj, kind=kind)
    raise AssertionError(f"unhandled backend kind {kind}")  # pragma: no cover
