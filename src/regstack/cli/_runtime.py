"""Helpers shared between the CLI commands.

Keeps the per-command modules small and focused on their click flag wiring.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from regstack.app import RegStack
from regstack.config.schema import RegStackConfig
from regstack.db.client import make_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def load_runtime_config(toml_path: Path | None = None) -> RegStackConfig:
    return RegStackConfig.load(toml_path=toml_path) if toml_path else RegStackConfig.load()


@asynccontextmanager
async def open_regstack(toml_path: Path | None = None) -> AsyncIterator[RegStack]:
    """Yield a fully-wired ``RegStack`` against a real Mongo connection.

    Both the connection and the regstack instance are torn down on exit so
    short-lived CLI invocations don't leak background tasks.
    """
    config = load_runtime_config(toml_path)
    mongo = make_client(config)
    try:
        db = mongo[config.mongodb_database]
        rs = RegStack(config=config, db=db)
        await rs.install_indexes()
        yield rs
    finally:
        await mongo.aclose()
