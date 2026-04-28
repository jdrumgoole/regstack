from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from regstack.auth.jwt import TokenError, is_payload_bulk_revoked

if TYPE_CHECKING:
    from regstack.auth.jwt import JwtCodec
    from regstack.backends.protocols import BlacklistRepoProtocol, UserRepoProtocol
    from regstack.models.user import BaseUser

_bearer = HTTPBearer(auto_error=False)


class AuthDependencies:
    """Factory for FastAPI auth dependencies, bound to one RegStack instance.

    A factory (rather than module-level dependencies) so two embedded
    RegStack instances in the same process don't share state via module
    globals — useful for multi-tenant deployments.

    Hosts that want to require authentication on their own endpoints
    use :meth:`current_user` and :meth:`current_admin` as FastAPI
    ``Depends(...)`` arguments::

        from fastapi import Depends

        @app.get("/me/orders")
        async def list_orders(user = Depends(regstack.deps.current_user())):
            ...
    """

    def __init__(
        self,
        *,
        jwt: JwtCodec,
        users: UserRepoProtocol,
        blacklist: BlacklistRepoProtocol,
    ) -> None:
        """Bind the factory to a codec and the user/blacklist repos.

        Args:
            jwt: The :class:`~regstack.auth.jwt.JwtCodec` used to decode
                bearer tokens.
            users: The user repository — looked up to confirm the
                ``sub`` claim still resolves to an active user.
            blacklist: The
                :class:`~regstack.backends.protocols.BlacklistRepoProtocol`
                used for per-token (``jti``) revocation checks.
        """
        self._jwt = jwt
        self._users = users
        self._blacklist = blacklist

    def current_user(self) -> object:
        """Return a FastAPI dependency that yields the authenticated user.

        The returned callable validates the ``Authorization: Bearer
        <token>`` header against the JWT codec, the per-token
        blacklist, and the user's bulk-revoke cutoff. On success the
        :class:`~regstack.models.user.BaseUser` is stashed on
        ``request.state.regstack_user`` for downstream middleware.

        Returns:
            A callable suitable for ``Depends(...)``. Raises
            :class:`fastapi.HTTPException` 401 on any auth failure.
        """

        async def _dep(
            request: Request,
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> BaseUser:
            user = await self._authenticate(creds)
            request.state.regstack_user = user
            return user

        return _dep

    def current_admin(self) -> object:
        """Return a FastAPI dependency that yields a *superuser*.

        Same checks as :meth:`current_user`, plus a 403 if the
        authenticated user does not have ``is_superuser=True``.

        Returns:
            A callable suitable for ``Depends(...)``. Raises 401 on
            auth failure or 403 on insufficient privilege.
        """

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
