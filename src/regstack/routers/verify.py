from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr

from regstack.auth.tokens import generate_verification_token, hash_token
from regstack.models.pending_registration import PendingRegistration
from regstack.models.user import BaseUser, UserPublic
from regstack.routers._schemas import MessageResponse

if TYPE_CHECKING:
    from regstack.app import RegStack


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


class ResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


def build_verify_router(rs: RegStack) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/verify",
        response_model=UserPublic,
        summary="Confirm an email address from a verification link",
    )
    async def verify(payload: VerifyRequest) -> UserPublic:
        token_hash_value = hash_token(payload.token)
        pending = await rs.pending.find_by_token_hash(token_hash_value)
        if pending is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification token is invalid or has expired.",
            )
        if pending.expires_at <= rs.clock.now():
            await rs.pending.delete_by_email(pending.email)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification token has expired. Request a new one.",
            )

        user = BaseUser(
            email=pending.email,
            hashed_password=pending.hashed_password,
            full_name=pending.full_name,
            is_active=True,
            is_verified=True,
        )
        user = await rs.users.create(user)
        await rs.pending.delete_by_email(pending.email)

        await rs.hooks.fire("user_verified", user=user)
        return UserPublic.from_user(user)

    @router.post(
        "/resend-verification",
        response_model=MessageResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Re-send a verification email if a pending registration exists",
    )
    async def resend(payload: ResendRequest) -> MessageResponse:
        # Anti-enumeration: always return the same response regardless of
        # whether a pending registration exists.
        existing_user = await rs.users.get_by_email(payload.email)
        if existing_user is not None:
            return _ack()

        pending = await rs.pending.find_by_email(payload.email)
        if pending is None:
            return _ack()

        raw, token_hash_value = generate_verification_token()
        ttl = rs.config.verification_token_ttl_seconds
        new_pending = PendingRegistration(
            id=None,
            email=pending.email,
            hashed_password=pending.hashed_password,
            full_name=pending.full_name,
            token_hash=token_hash_value,
            expires_at=rs.clock.now() + timedelta(seconds=ttl),
        )
        new_pending.created_at = datetime.now(UTC)
        await rs.pending.upsert(new_pending)

        url = _verification_url(rs, raw)
        message = rs.mail.verification(
            to=pending.email,
            full_name=pending.full_name,
            url=url,
        )
        await rs.email.send(message)
        await rs.hooks.fire("verification_requested", email=pending.email, url=url)
        return _ack()

    return router


def _ack() -> MessageResponse:
    return MessageResponse(message="If a pending registration exists, a new email has been sent.")


def _verification_url(rs: RegStack, raw_token: str) -> str:
    base = str(rs.config.base_url).rstrip("/")
    return f"{base}/verify?token={raw_token}"


__all__ = ["ResendRequest", "VerifyRequest", "build_verify_router"]
