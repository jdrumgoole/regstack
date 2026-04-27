from __future__ import annotations

from typing import Annotated, Any

from bson import ObjectId
from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema


class _ObjectIdValidator:
    """Pydantic v2 type that accepts an ``ObjectId`` or its 24-char hex string and serialises as str."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        def validate(value: Any) -> str:
            if isinstance(value, ObjectId):
                return str(value)
            if isinstance(value, str) and ObjectId.is_valid(value):
                return value
            raise ValueError(f"Not a valid ObjectId: {value!r}")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )


ObjectIdStr = Annotated[str, _ObjectIdValidator]
