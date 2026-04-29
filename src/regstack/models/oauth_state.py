"""Server-side state row for an in-flight OAuth flow.

PKCE's ``code_verifier`` must stay server-side; we keep it (plus the
target redirect, the flow mode, and the linking user when applicable)
in this row, addressed by a random ``id``. The OAuth ``state``
parameter the browser carries is just that ID.

After the callback completes, the same row is updated with
``result_token`` (the session JWT). The SPA exchanges its ID for the
token via ``POST /oauth/exchange``; the row is deleted on first
exchange. Anything still sitting around past ``expires_at`` is
reaped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from regstack.models._objectid import IdStr


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _five_min_hence() -> datetime:
    return _utcnow() + timedelta(minutes=5)


OAuthFlowMode = Literal["signin", "link"]


class OAuthState(BaseModel):
    """One row per in-flight OAuth flow."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: IdStr = Field(alias="_id")
    """The state-parameter value the browser carries through Google.
    Always supplied by the caller; this is the lookup key."""

    provider: str
    """Provider name — matches ``OAuthProvider.name``."""

    code_verifier: str
    """PKCE pre-image. Never leaves the server."""

    nonce: str
    """Random string echoed in the ID token. Defends against ID-token
    replay across separate authentication ceremonies."""

    redirect_to: str
    """Where the SPA should land after the OAuth flow completes.
    Validated against ``config.base_url`` at /start time so the
    callback can't be coerced into an open redirect."""

    mode: OAuthFlowMode
    """``"signin"`` for unauthenticated start, ``"link"`` for adding a
    provider to a logged-in account."""

    linking_user_id: IdStr | None = None
    """Set on ``mode = "link"`` flows. The user the new identity gets
    attached to. Captured at /start time from the bearer token, so the
    callback (which has no auth header) doesn't have to re-authenticate."""

    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime = Field(default_factory=_five_min_hence)
    """TTL boundary. Mongo's ``expireAfterSeconds`` reaps rows; SQL
    backends rely on read-side ``expires_at > now()`` plus the optional
    ``purge_expired`` reaper."""

    result_token: str | None = None
    """Populated on a successful callback. The SPA exchanges its
    ``id`` for this value via ``POST /oauth/exchange``; the row is
    deleted on exchange."""

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=True)
        return data
