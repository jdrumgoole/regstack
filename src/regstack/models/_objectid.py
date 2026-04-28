"""ID column type used by every persisted model.

The contract is "any non-empty string" — concrete formats are decided by
the backend:

- Mongo backend hands back ``str(ObjectId)`` (24-char hex).
- SQL backends use UUID4 hex (32-char).
- Hosts with their own ID strategy can substitute anything else as long
  as it round-trips as a string.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

# bson is imported lazily so the package remains importable on a base
# install (the `mongo` extra is what pulls pymongo, and pymongo is the
# only consumer of bson). When the Mongo backend is in use, bson is
# always present.
try:
    from bson import ObjectId as _BsonObjectId  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — exercised only without the mongo extra
    _BsonObjectId = None  # type: ignore[assignment,misc]


class _IdValidator:
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        def validate(value: Any) -> str:
            if _BsonObjectId is not None and isinstance(value, _BsonObjectId):
                return str(value)
            if isinstance(value, str) and value:
                return value
            raise ValueError(f"Not a valid id: {value!r}")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )


IdStr = Annotated[str, _IdValidator]
