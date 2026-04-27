"""Custom SQLAlchemy types for cross-database tz-aware datetimes.

SQLite has no native timezone-aware type — even ``DateTime(timezone=True)``
returns naive ``datetime`` objects on read. Postgres respects timezone
on its TIMESTAMPTZ column. This TypeDecorator normalises both: writes
convert naive → UTC-aware, reads attach UTC if the driver dropped it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, TypeDecorator


class UtcDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"UtcDateTime expects datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
