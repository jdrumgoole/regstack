"""External OAuth identity linked to a regstack user.

One row per ``(provider, subject_id)``. Both
``(provider, subject_id)`` and ``(user_id, provider)`` are unique:

- The first stops two regstack users from sharing one external
  identity (otherwise a single Google account could log in as
  whichever regstack user the lookup happened to find first).
- The second stops one regstack user from linking two identities of
  the same provider (no use case in the UI; would only confuse the
  /account/me listing).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from regstack.models._objectid import IdStr


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OAuthIdentity(BaseModel):
    """A user's link to one external OAuth provider."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: IdStr | None = Field(default=None, alias="_id")

    user_id: IdStr
    """The regstack user this identity belongs to."""

    provider: str
    """Provider name — matches ``OAuthProvider.name`` (e.g. ``"google"``)."""

    subject_id: str
    """The provider's stable, opaque user identifier. Never an email."""

    email: str | None = None
    """Snapshot of the provider's email at link time. Non-authoritative —
    the provider may change it. We never key on this field."""

    linked_at: datetime = Field(default_factory=_utcnow)
    last_used_at: datetime | None = None

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=True)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data
