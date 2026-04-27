from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from regstack.models._objectid import ObjectIdStr


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PendingRegistration(BaseModel):
    """Pre-verification user record. Lives in `pending_registrations` until
    the user clicks the verification link (which moves them to `users`) or
    `expires_at` passes (TTL index reaps).

    Only the SHA-256 hash of the verification token is stored — the raw
    token only exists in the email body and the user's clipboard.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: ObjectIdStr | None = Field(default=None, alias="_id")
    email: EmailStr
    hashed_password: str
    full_name: str | None = None
    token_hash: str
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=True)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data
