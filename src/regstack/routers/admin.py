from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field

from regstack.auth.tokens import generate_verification_token
from regstack.models.pending_registration import PendingRegistration
from regstack.models.user import BaseUser, UserPublic
from regstack.routers._schemas import MessageResponse

if TYPE_CHECKING:
    from regstack.app import RegStack


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=200)


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    verified_users: int
    superusers: int
    pending_registrations: int


class UserListResponse(BaseModel):
    items: list[UserPublic]
    total: int
    skip: int
    limit: int


def build_admin_router(rs: RegStack) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["regstack-admin"])

    @router.get(
        "/stats",
        response_model=AdminStats,
        summary="Aggregate counts for the admin dashboard",
    )
    async def stats(_admin: BaseUser = Depends(rs.deps.current_admin())) -> AdminStats:
        total = await rs.users.count()
        active = await rs.users.count(is_active=True)
        verified = await rs.users.count(is_verified=True)
        supers = await rs.users.count(is_superuser=True)
        # PendingRegistrations don't have a count() yet — peek through the
        # mongo backend; SQL backends will add a typed count too.
        pending_count_doc = getattr(rs.pending, "_collection", None)
        if pending_count_doc is not None:
            pending = await pending_count_doc.count_documents({})
        else:  # pragma: no cover — used by SQL backends in Phase 2
            pending = 0
        return AdminStats(
            total_users=total,
            active_users=active,
            verified_users=verified,
            superusers=supers,
            pending_registrations=pending,
        )

    @router.get(
        "/users",
        response_model=UserListResponse,
        summary="List users (paginated)",
    )
    async def list_users(
        skip: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        _admin: BaseUser = Depends(rs.deps.current_admin()),
    ) -> UserListResponse:
        users = await rs.users.list_paged(skip=skip, limit=limit)
        total = await rs.users.count()
        return UserListResponse(
            items=[UserPublic.from_user(u) for u in users],
            total=total,
            skip=skip,
            limit=limit,
        )

    @router.get(
        "/users/{user_id}",
        response_model=UserPublic,
        summary="Fetch a single user by id",
    )
    async def get_user(
        user_id: str = Path(...),
        _admin: BaseUser = Depends(rs.deps.current_admin()),
    ) -> UserPublic:
        user = await _require_user(rs, user_id)
        return UserPublic.from_user(user)

    @router.patch(
        "/users/{user_id}",
        response_model=UserPublic,
        summary="Update mutable user flags",
    )
    async def update_user(
        payload: AdminUserUpdate,
        user_id: str = Path(...),
        _admin: BaseUser = Depends(rs.deps.current_admin()),
    ) -> UserPublic:
        user = await _require_user(rs, user_id)
        assert user.id is not None

        if payload.is_active is not None:
            await rs.users.set_active(user.id, is_active=payload.is_active)
            user.is_active = payload.is_active
            if payload.is_active is False:
                # A disabled user should not retain a live session.
                await rs.users.set_tokens_invalidated_after(user.id, rs.clock.now())
        if payload.is_superuser is not None:
            await rs.users.set_superuser(user.id, is_superuser=payload.is_superuser)
            user.is_superuser = payload.is_superuser
        if payload.full_name is not None:
            await rs.users.set_full_name(user.id, payload.full_name)
            user.full_name = payload.full_name
        return UserPublic.from_user(user)

    @router.delete(
        "/users/{user_id}",
        response_model=MessageResponse,
        summary="Permanently delete a user",
    )
    async def delete_user(
        user_id: str = Path(...),
        _admin: BaseUser = Depends(rs.deps.current_admin()),
    ) -> MessageResponse:
        user = await _require_user(rs, user_id)
        assert user.id is not None
        await rs.users.delete(user.id)
        await rs.pending.delete_by_email(user.email)
        await rs.lockout.clear(user.email)
        await rs.hooks.fire("user_deleted", user=user)
        return MessageResponse(message=f"User {user.email} deleted.")

    @router.post(
        "/users/{user_id}/resend-verification",
        response_model=MessageResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Re-send a verification email for an unverified user",
    )
    async def admin_resend_verification(
        user_id: str = Path(...),
        _admin: BaseUser = Depends(rs.deps.current_admin()),
    ) -> MessageResponse:
        user = await _require_user(rs, user_id)
        if user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is already verified.",
            )

        # Move the user to a pending registration row so the standard verify
        # endpoint completes the flow. Less special-case code, one path.
        raw, token_hash = generate_verification_token()
        ttl = rs.config.verification_token_ttl_seconds
        pending = PendingRegistration(
            email=user.email,
            hashed_password=user.hashed_password,
            full_name=user.full_name,
            token_hash=token_hash,
            expires_at=rs.clock.now() + timedelta(seconds=ttl),
        )
        await rs.pending.upsert(pending)

        base = str(rs.config.base_url).rstrip("/")
        url = f"{base}/verify?token={raw}"
        message = rs.mail.verification(to=user.email, full_name=user.full_name, url=url)
        await rs.email.send(message)
        await rs.hooks.fire("verification_requested", email=user.email, url=url)
        return MessageResponse(message=f"Verification email sent to {user.email}.")

    return router


async def _require_user(rs: RegStack, user_id: str) -> BaseUser:
    user = await rs.users.get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user


__all__ = ["AdminStats", "AdminUserUpdate", "UserListResponse", "build_admin_router"]
