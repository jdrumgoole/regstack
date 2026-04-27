from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status

from regstack.auth.tokens import generate_verification_token
from regstack.db.repositories.pending_repo import PendingAlreadyExistsError
from regstack.db.repositories.user_repo import UserAlreadyExistsError
from regstack.models.pending_registration import PendingRegistration
from regstack.models.user import BaseUser, UserCreate, UserPublic
from regstack.routers._schemas import PendingResponse

if TYPE_CHECKING:
    from regstack.app import RegStack


def build_register_router(rs: RegStack) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/register",
        response_model=UserPublic | PendingResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Register a new user (or start verification, if required)",
    )
    async def register(payload: UserCreate) -> UserPublic | PendingResponse:
        if not rs.config.allow_registration:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Registration is disabled.",
            )

        existing = await rs.users.get_by_email(payload.email)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with that email already exists.",
            )

        hashed = rs.password_hasher.hash(payload.password)

        if rs.config.require_verification:
            return await _start_verification(rs, payload, hashed)

        user = BaseUser(
            email=payload.email,
            hashed_password=hashed,
            full_name=payload.full_name,
            is_verified=True,
        )
        try:
            user = await rs.users.create(user)
        except UserAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with that email already exists.",
            ) from exc

        await rs.hooks.fire("user_registered", user=user)
        return UserPublic.from_user(user)

    return router


async def _start_verification(
    rs: RegStack, payload: UserCreate, hashed_password: str
) -> PendingResponse:
    raw, token_hash_value = generate_verification_token()
    ttl = rs.config.verification_token_ttl_seconds
    expires_at = rs.clock.now() + timedelta(seconds=ttl)
    pending = PendingRegistration(
        email=payload.email,
        hashed_password=hashed_password,
        full_name=payload.full_name,
        token_hash=token_hash_value,
        expires_at=expires_at,
    )
    try:
        # upsert lets a user re-attempt registration; the most recent token wins
        # and the old verification link silently stops working.
        pending = await rs.pending.upsert(pending)
    except PendingAlreadyExistsError as exc:  # pragma: no cover — upsert can't raise this
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending registration already exists for that email.",
        ) from exc

    base = str(rs.config.base_url).rstrip("/")
    url = f"{base}/verify?token={raw}"
    message = rs.mail.verification(
        to=payload.email,
        full_name=payload.full_name,
        url=url,
    )
    await rs.email.send(message)
    await rs.hooks.fire("verification_requested", email=payload.email, url=url)
    return PendingResponse(email=payload.email, expires_at=expires_at.isoformat())
