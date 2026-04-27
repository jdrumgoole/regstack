"""ID column type used by every persisted model.

The contract is "any non-empty string" — concrete formats are decided by
the backend:

- Mongo backend hands back ``str(ObjectId)`` (24-char hex).
- SQL backends use UUID4 hex (32-char).
- Hosts with their own ID strategy can substitute anything else as long
  as it round-trips as a string.

Historically this was named ``ObjectIdStr`` and validated 24-char hex
only. The rename and relaxed contract landed when the SQL backends
arrived; the old name is kept as an alias for any external imports.
"""

from __future__ import annotations

from typing import Annotated, Any

from bson import ObjectId
from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema


class _IdValidator:
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        def validate(value: Any) -> str:
            if isinstance(value, ObjectId):
                return str(value)
            if isinstance(value, str) and value:
                return value
            raise ValueError(f"Not a valid id: {value!r}")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )


IdStr = Annotated[str, _IdValidator]

# Back-compat alias — the contract is now "any non-empty string", so
# anyone reaching for ObjectIdStr keeps the same surface area.
ObjectIdStr = IdStr
