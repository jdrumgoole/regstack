from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from regstack.models._objectid import IdStr


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LoginAttempt(BaseModel):
    """One row per failed login attempt. TTL index on ``when`` reaps rows
    once they fall outside the lockout window.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: IdStr | None = Field(default=None, alias="_id")
    email: EmailStr
    when: datetime = Field(default_factory=_utcnow)
    ip: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=True)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data
