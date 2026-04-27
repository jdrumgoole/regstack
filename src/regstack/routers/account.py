from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import jwt as pyjwt
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from regstack.auth.jwt import TokenError
from regstack.config.secrets import derive_secret
from regstack.db.repositories.user_repo import UserAlreadyExistsError
from regstack.models.user import BaseUser, UserPublic
from regstack.routers._schemas import MessageResponse, PasswordStr

if TYPE_CHECKING:
    from regstack.app import RegStack


_EMAIL_CHANGE_PURPOSE = "email_change"
_NEW_EMAIL_CLAIM = "new_email"


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: PasswordStr


class ChangeEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_email: EmailStr
    current_password: str


class ConfirmEmailChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str


class UpdateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, max_length=200)


def build_account_router(rs: RegStack) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/me",
        response_model=UserPublic,
        summary="Return the authenticated user",
    )
    async def me(user: BaseUser = Depends(rs.deps.current_user())) -> UserPublic:
        return UserPublic.from_user(user)

    @router.patch(
        "/me",
        response_model=UserPublic,
        summary="Update the authenticated user's profile fields",
    )
    async def update_me(
        payload: UpdateProfileRequest,
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> UserPublic:
        assert user.id is not None
        await rs.users.set_full_name(user.id, payload.full_name)
        user.full_name = payload.full_name
        return UserPublic.from_user(user)

    @router.post(
        "/change-password",
        response_model=MessageResponse,
        summary="Change the authenticated user's password (revokes existing sessions)",
    )
    async def change_password(
        payload: ChangePasswordRequest,
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> MessageResponse:
        if not rs.password_hasher.verify(payload.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )
        if rs.password_hasher.verify(payload.new_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must differ from the current password.",
            )

        new_hash = rs.password_hasher.hash(payload.new_password)
        assert user.id is not None
        await rs.users.update_password(user.id, new_hash)
        await rs.lockout.clear(user.email)
        await rs.hooks.fire("password_changed", user=user)
        return MessageResponse(message="Password changed. Existing sessions have been signed out.")

    @router.post(
        "/change-email",
        response_model=MessageResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Request an email-address change (sends confirmation to new address)",
    )
    async def change_email(
        payload: ChangeEmailRequest,
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> MessageResponse:
        if payload.new_email.lower() == user.email.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New email is the same as the current email.",
            )
        if not rs.password_hasher.verify(payload.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )
        clash = await rs.users.get_by_email(payload.new_email)
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That email address is already in use.",
            )

        assert user.id is not None
        ttl = rs.config.email_change_token_ttl_seconds
        token = _encode_email_change_token(rs, user.id, payload.new_email, ttl)
        url = _email_change_url(rs, token)
        message = rs.mail.email_change(
            to=payload.new_email,
            full_name=user.full_name,
            url=url,
            ttl_minutes=max(ttl // 60, 1),
        )
        await rs.email.send(message)
        await rs.hooks.fire(
            "email_change_requested",
            user=user,
            new_email=payload.new_email,
            url=url,
        )
        return MessageResponse(
            message="A confirmation link has been sent to the new email address."
        )

    @router.post(
        "/confirm-email-change",
        response_model=UserPublic,
        summary="Consume an email-change token and swap the address",
    )
    async def confirm_email_change(payload: ConfirmEmailChangeRequest) -> UserPublic:
        try:
            user_id, new_email = _decode_email_change_token(rs, payload.token)
        except TokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token is invalid or has expired.",
            ) from exc

        user = await rs.users.get_by_id(user_id)
        if user is None or not user.is_active or user.id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token does not match an active account.",
            )

        try:
            await rs.users.update_email(user.id, new_email)
        except UserAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That email address is already in use.",
            ) from exc

        previous_email = user.email
        user.email = new_email
        await rs.lockout.clear(previous_email)
        await rs.hooks.fire("email_changed", user=user, previous_email=previous_email)
        return UserPublic.from_user(user)

    @router.delete(
        "/account",
        response_model=MessageResponse,
        summary="Permanently delete the authenticated user's account",
    )
    async def delete_account(
        payload: Annotated[DeleteAccountRequest, Body()],
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> MessageResponse:
        if not rs.config.enable_account_deletion:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account deletion is disabled for this application.",
            )
        if not rs.password_hasher.verify(payload.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )

        assert user.id is not None
        await rs.users.delete(user.id)
        await rs.pending.delete_by_email(user.email)
        await rs.lockout.clear(user.email)
        await rs.hooks.fire("user_deleted", user=user)
        return MessageResponse(message="Account deleted.")

    return router


def _encode_email_change_token(rs: RegStack, user_id: str, new_email: str, ttl: int) -> str:
    """Mint a JWT carrying both ``sub=user_id`` and a ``new_email`` claim.

    Goes through pyjwt directly (rather than ``rs.jwt.encode``) because we
    need a custom claim. Same per-purpose key derivation though, so this
    token is unforgeable from a session token.
    """
    import secrets as _secrets

    now = rs.clock.now()
    from datetime import timedelta

    payload: dict[str, Any] = {
        "sub": user_id,
        "jti": _secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "purpose": _EMAIL_CHANGE_PURPOSE,
        _NEW_EMAIL_CLAIM: new_email,
    }
    if rs.config.jwt_audience is not None:
        payload["aud"] = rs.config.jwt_audience
    key = derive_secret(rs.config.jwt_secret.get_secret_value(), _EMAIL_CHANGE_PURPOSE)
    return pyjwt.encode(payload, key, algorithm=rs.config.jwt_algorithm)


def _decode_email_change_token(rs: RegStack, token: str) -> tuple[str, str]:
    key = derive_secret(rs.config.jwt_secret.get_secret_value(), _EMAIL_CHANGE_PURPOSE)
    try:
        claims = pyjwt.decode(
            token,
            key,
            algorithms=[rs.config.jwt_algorithm],
            audience=rs.config.jwt_audience,
            options={
                "require": ["sub", "exp", "iat", "jti", "purpose", _NEW_EMAIL_CLAIM],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except pyjwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if claims.get("purpose") != _EMAIL_CHANGE_PURPOSE:
        raise TokenError("token purpose mismatch")

    from datetime import datetime

    now = rs.clock.now()
    exp = datetime.fromtimestamp(int(claims["exp"]), tz=now.tzinfo)
    if now >= exp:
        raise TokenError("Token has expired")

    return str(claims["sub"]), str(claims[_NEW_EMAIL_CLAIM])


def _email_change_url(rs: RegStack, token: str) -> str:
    base = str(rs.config.base_url).rstrip("/")
    return f"{base}/confirm-email-change?token={token}"
