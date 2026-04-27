from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status

from regstack.auth.jwt import TokenError
from regstack.routers._schemas import MessageResponse

if TYPE_CHECKING:
    from regstack.app import RegStack


def build_logout_router(rs: RegStack) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/logout",
        response_model=MessageResponse,
        status_code=status.HTTP_200_OK,
        summary="Revoke the current bearer token",
    )
    async def logout(
        request: Request,
        _user=Depends(rs.deps.current_user()),
    ) -> MessageResponse:
        # Re-decode the token (the dep already validated it) to grab jti+exp
        # so we can record the revocation. The auth header was already proven
        # well-formed; this decode cannot raise.
        auth = request.headers.get("authorization", "")
        token = auth.split(" ", 1)[1] if " " in auth else ""
        try:
            payload = rs.jwt.decode(token)
        except TokenError:
            return MessageResponse(message="Logged out.")
        await rs.blacklist.revoke(payload.jti, payload.exp)
        return MessageResponse(message="Logged out.")

    return router
