from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from regstack.auth.jwt import TokenError, is_payload_bulk_revoked

if TYPE_CHECKING:
    from regstack.auth.jwt import JwtCodec
    from regstack.backends.mongo.repositories.blacklist_repo import BlacklistRepo
    from regstack.backends.mongo.repositories.user_repo import UserRepo
    from regstack.models.user import BaseUser

_bearer = HTTPBearer(auto_error=False)


class AuthDependencies:
    """Factory for FastAPI dependencies bound to a particular RegStack instance.

    A factory is used (rather than module-level dependencies) because two
    embedded RegStack instances in the same process would otherwise share
    state via module globals.
    """

    def __init__(
        self,
        *,
        jwt: JwtCodec,
        users: UserRepo,
        blacklist: BlacklistRepo,
    ) -> None:
        self._jwt = jwt
        self._users = users
        self._blacklist = blacklist

    def current_user(self) -> object:
        """Return a callable usable as a FastAPI dependency yielding the authenticated user."""

        async def _dep(
            request: Request,
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> BaseUser:
            user = await self._authenticate(creds)
            request.state.regstack_user = user
            return user

        return _dep

    def current_admin(self) -> object:
        async def _dep(
            request: Request,
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> BaseUser:
            user = await self._authenticate(creds)
            if not user.is_superuser:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Administrator privileges required.",
                )
            request.state.regstack_user = user
            return user

        return _dep

    async def _authenticate(self, creds: HTTPAuthorizationCredentials | None):
        if creds is None or creds.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            payload = self._jwt.decode(creds.credentials)
        except TokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        if await self._blacklist.is_revoked(payload.jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked.",
            )

        user = await self._users.get_by_id(payload.sub)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User no longer active.",
            )

        if is_payload_bulk_revoked(payload, user.tokens_invalidated_after):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session was invalidated; please sign in again.",
            )

        return user
