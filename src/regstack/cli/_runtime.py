"""Helpers shared between the CLI commands.

Keeps the per-command modules small and focused on their click flag wiring.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from regstack.app import RegStack
from regstack.config.schema import RegStackConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def load_runtime_config(toml_path: Path | None = None) -> RegStackConfig:
    return RegStackConfig.load(toml_path=toml_path) if toml_path else RegStackConfig.load()


@asynccontextmanager
async def open_regstack(toml_path: Path | None = None) -> AsyncIterator[RegStack]:
    """Yield a fully-wired ``RegStack`` against the configured backend.

    The backend's connection pool is torn down on exit so short-lived CLI
    invocations don't leak background tasks. Backend selection (Mongo,
    SQLite, Postgres) follows ``config.database_url``.
    """
    config = load_runtime_config(toml_path)
    rs = RegStack(config=config)
    try:
        await rs.install_schema()
        yield rs
    finally:
        await rs.aclose()
