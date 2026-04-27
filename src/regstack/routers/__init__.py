from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from regstack.routers.account import build_account_router
from regstack.routers.admin import build_admin_router
from regstack.routers.login import build_login_router
from regstack.routers.logout import build_logout_router
from regstack.routers.password import build_password_router
from regstack.routers.phone import build_phone_router
from regstack.routers.register import build_register_router
from regstack.routers.verify import build_verify_router

if TYPE_CHECKING:
    from regstack.app import RegStack


def build_router(rs: RegStack) -> APIRouter:
    """Compose the JSON router that hosts mount via ``app.include_router``."""
    router = APIRouter(tags=["regstack"])
    router.include_router(build_register_router(rs))
    router.include_router(build_verify_router(rs))
    router.include_router(build_login_router(rs))
    router.include_router(build_logout_router(rs))
    router.include_router(build_account_router(rs))
    if rs.config.enable_password_reset:
        router.include_router(build_password_router(rs))
    if rs.config.enable_sms_2fa:
        router.include_router(build_phone_router(rs))
    if rs.config.enable_admin_router:
        router.include_router(build_admin_router(rs))
    return router


__all__ = ["build_admin_router", "build_router"]
