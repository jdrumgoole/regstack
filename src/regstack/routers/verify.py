from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr

from regstack.auth.tokens import generate_verification_token, hash_token
from regstack.backends.protocols import UserAlreadyExistsError
from regstack.hooks.redaction import redact_token
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
                detail="Verification link is invalid or has expired. Request a new one.",
            )
        if pending.expires_at <= rs.clock.now():
            await rs.pending.delete_by_email(pending.email)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification link is invalid or has expired. Request a new one.",
            )

        user = BaseUser(
            email=pending.email,
            hashed_password=pending.hashed_password,
            full_name=pending.full_name,
            is_active=True,
            is_verified=True,
        )
        try:
            user = await rs.users.create(user)
        except UserAlreadyExistsError as exc:
            # An admin's `promote_pending` call (or a duplicate
            # `POST /verify` racing with itself) can have created the
            # user between our find-pending and now. Both paths leave
            # the user in the correct end-state, so surface a friendly
            # 400 rather than letting the unique-key violation bubble
            # up as a 500.
            await rs.pending.delete_by_email(pending.email)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This email is already registered. Please sign in.",
            ) from exc
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
        # Set created_at via rs.clock so FrozenClock-driven tests see
        # the same instant on both sides of the resend; the model's
        # default factory uses wall-clock `datetime.now(UTC)` which
        # would drift under a frozen clock.
        new_pending = PendingRegistration(
            id=None,
            email=pending.email,
            hashed_password=pending.hashed_password,
            full_name=pending.full_name,
            token_hash=token_hash_value,
            created_at=rs.clock.now(),
            expires_at=rs.clock.now() + timedelta(seconds=ttl),
        )
        await rs.pending.upsert(new_pending)

        url = _verification_url(rs, raw)
        message = rs.mail.verification(
            to=pending.email,
            full_name=pending.full_name,
            url=url,
            ttl_hours=max(ttl // 3600, 1),
        )
        await rs.email.send(message)
        await rs.hooks.fire(
            "verification_requested",
            email=pending.email,
            url=url,
            url_without_token=redact_token(url, raw),
        )
        return _ack()

    return router


def _ack() -> MessageResponse:
    return MessageResponse(message="If a pending registration exists, a new email has been sent.")


def _verification_url(rs: RegStack, raw_token: str) -> str:
    return rs.config.resolve_verify_url(raw_token)


__all__ = ["ResendRequest", "VerifyRequest", "build_verify_router"]
