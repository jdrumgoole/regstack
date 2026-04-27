from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from regstack.models._objectid import ObjectIdStr

MfaKind = Literal["phone_setup", "login_mfa"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MfaCode(BaseModel):
    """One-time SMS code awaiting verification.

    Only the SHA-256 hash of the code lives in the DB — a database read does
    not yield usable codes. Codes are unique per ``(user_id, kind)`` so
    re-issuing a code automatically invalidates the previous one.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: ObjectIdStr | None = Field(default=None, alias="_id")
    user_id: str
    kind: MfaKind
    code_hash: str
    expires_at: datetime
    attempts: int = 0
    max_attempts: int = 5
    created_at: datetime = Field(default_factory=_utcnow)

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=True)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data
