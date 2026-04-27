from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pymongo import AsyncMongoClient

if TYPE_CHECKING:
    from regstack.config.schema import RegStackConfig


def make_client(config: RegStackConfig) -> AsyncMongoClient:
    """Build an AsyncMongoClient with the settings regstack expects.

    ``tz_aware=True`` makes BSON datetimes round-trip as UTC-aware values; the
    JWT and bulk-revocation comparisons assume aware datetimes throughout.
    """
    return AsyncMongoClient(
        config.database_url.get_secret_value(),
        tz_aware=True,
    )


def parse_database_name(config: RegStackConfig) -> str:
    """Pull the database name out of the Mongo URL path, falling back to
    ``mongodb_database`` for callers that prefer the explicit field.
    """
    url = config.database_url.get_secret_value()
    parts = urlsplit(url)
    name = (parts.path or "").lstrip("/")
    if name:
        return name
    if config.mongodb_database:
        return config.mongodb_database
    raise ValueError(
        "MongoDB URL has no database path and config.mongodb_database is unset. "
        "Use mongodb://host:port/dbname or set mongodb_database."
    )
