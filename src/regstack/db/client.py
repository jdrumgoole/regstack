from __future__ import annotations

from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

    from regstack.config.schema import RegStackConfig


def make_client(config: RegStackConfig) -> AsyncMongoClient:
    """Build an AsyncMongoClient with the settings regstack expects.

    ``tz_aware=True`` makes BSON datetimes round-trip as UTC-aware values; the
    JWT and bulk-revocation comparisons assume aware datetimes throughout.
    """
    return AsyncMongoClient(
        config.mongodb_url.get_secret_value(),
        tz_aware=True,
    )


def get_database(client: AsyncMongoClient, config: RegStackConfig) -> AsyncDatabase:
    return client[config.mongodb_database]
