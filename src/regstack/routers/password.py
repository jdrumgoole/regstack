from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from regstack.auth.jwt import TokenError
from regstack.hooks.redaction import redact_token
from regstack.routers._schemas import MessageResponse, PasswordStr

if TYPE_CHECKING:
    from regstack.app import RegStack


_PASSWORD_RESET_PURPOSE = "password_reset"


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    new_password: PasswordStr = Field(alias="new_password")


def build_password_router(rs: RegStack) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/forgot-password",
        response_model=MessageResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Request a password-reset link (always succeeds)",
    )
    async def forgot(payload: ForgotPasswordRequest) -> MessageResponse:
        if not rs.config.enable_password_reset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Password reset is disabled for this application.",
            )

        # Anti-enumeration: same response regardless of whether the user exists.
        ack = MessageResponse(
            message=(
                "If the address matches an active account, a reset link has been sent. "
                "Check your email."
            )
        )
        user = await rs.users.get_by_email(payload.email)
        if user is None or user.id is None or not user.is_active:
            return ack

        ttl = rs.config.password_reset_token_ttl_seconds
        token, _ = rs.jwt.encode(
            user.id,
            purpose=_PASSWORD_RESET_PURPOSE,
            ttl_seconds=ttl,
        )
        url = _reset_url(rs, token)
        message = rs.mail.password_reset(
            to=user.email,
            full_name=user.full_name,
            url=url,
            ttl_minutes=max(ttl // 60, 1),
        )
        await rs.email.send(message)
        await rs.hooks.fire(
            "password_reset_requested",
            user=user,
            url=url,
            url_without_token=redact_token(url, token),
        )
        return ack

    @router.post(
        "/reset-password",
        response_model=MessageResponse,
        summary="Consume a password-reset link and set a new password",
    )
    async def reset(payload: ResetPasswordRequest) -> MessageResponse:
        if not rs.config.enable_password_reset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Password reset is disabled for this application.",
            )

        try:
            token_payload = rs.jwt.decode(payload.token, purpose=_PASSWORD_RESET_PURPOSE)
        except TokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset link is invalid or has expired. Request a new one.",
            ) from exc

        # Refuse to re-use a reset token. The token's jti is added to the
        # blacklist on first success, so a second click of the same link
        # gets the same "invalid or has expired" response that an
        # expired-token attempt sees. (Review #17.)
        if await rs.blacklist.is_revoked(token_payload.jti):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset link is invalid or has expired. Request a new one.",
            )

        user = await rs.users.get_by_id(token_payload.sub)
        if user is None or user.id is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reset link does not match an active account.",
            )

        new_hash = rs.password_hasher.hash(payload.new_password)
        # update_password also bumps tokens_invalidated_after, which bulk-revokes
        # every outstanding session — this is essential after a reset because a
        # stolen session token would otherwise outlive the password change.
        await rs.users.update_password(user.id, new_hash)
        await rs.lockout.clear(user.email)
        # Blacklist the reset token so the same link can't be used twice.
        await rs.blacklist.revoke(token_payload.jti, token_payload.exp)
        await rs.hooks.fire("password_reset_completed", user=user)
        return MessageResponse(message="Password has been reset. Please sign in.")

    return router


def _reset_url(rs: RegStack, token: str) -> str:
    return rs.config.resolve_password_reset_url(token)


__all__ = ["ForgotPasswordRequest", "ResetPasswordRequest", "build_password_router"]
