from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PasswordStr = Annotated[str, Field(min_length=8, max_length=128)]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: PasswordStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MessageResponse(BaseModel):
    message: str


class PendingResponse(BaseModel):
    """Returned when registration starts a verification flow rather than
    creating a logged-in user immediately. The host can use this to redirect
    to a "check your email" page.
    """

    status: str = "pending_verification"
    email: EmailStr
    expires_at: str  # ISO-8601 UTC, JSON-friendly
