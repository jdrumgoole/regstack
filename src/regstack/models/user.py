from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from regstack.models._objectid import ObjectIdStr


def _utcnow() -> datetime:
    return datetime.now(UTC)


PasswordStr = Annotated[str, Field(min_length=8, max_length=128)]


class BaseUser(BaseModel):
    """Persisted user document.

    The default field set covers what both winebox and putplace need today.
    Hosts add their own fields by subclassing or by registering an extension
    mixin via ``RegStack.extend_user_model``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: ObjectIdStr | None = Field(default=None, alias="_id")
    email: EmailStr
    hashed_password: str
    is_active: bool = True
    is_verified: bool = False
    is_superuser: bool = False
    full_name: str | None = None
    phone_number: str | None = None
    is_mfa_enabled: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_login: datetime | None = None
    tokens_invalidated_after: datetime | None = None

    @property
    def is_admin(self) -> bool:
        """Alias kept for parity with putplace's ``is_admin`` field name."""
        return self.is_superuser

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=True)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: PasswordStr
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("password")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=200)


class UserPublic(BaseModel):
    """Safe-to-serialise projection of a user (no password hash)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    email: EmailStr
    is_active: bool
    is_verified: bool
    is_superuser: bool
    full_name: str | None = None
    phone_number: str | None = None
    is_mfa_enabled: bool = False
    created_at: datetime
    last_login: datetime | None = None

    @classmethod
    def from_user(cls, user: BaseUser) -> UserPublic:
        if user.id is None:
            raise ValueError("Cannot serialise a user without an id")
        return cls(
            _id=user.id,
            email=user.email,
            is_active=user.is_active,
            is_verified=user.is_verified,
            is_superuser=user.is_superuser,
            full_name=user.full_name,
            phone_number=user.phone_number,
            is_mfa_enabled=user.is_mfa_enabled,
            created_at=user.created_at,
            last_login=user.last_login,
        )
