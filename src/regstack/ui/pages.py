from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, select_autoescape

if TYPE_CHECKING:
    from regstack.app import RegStack

_PACKAGE = "regstack.ui"
_TEMPLATE_DIR = "templates"
_STATIC_DIR = "static"

PAGE_NAMES = (
    "login",
    "register",
    "verify",
    "forgot",
    "reset",
    "me",
    "confirm-email-change",
    "mfa-confirm",
)


def default_static_dir() -> Path:
    """Filesystem path to the bundled static assets — used by the StaticFiles
    factory in :mod:`regstack.app`.
    """
    return Path(str(resources.files(_PACKAGE).joinpath(_STATIC_DIR)))


def build_ui_environment(host_template_dirs: list[Path] | None = None) -> Environment:
    loaders = [FileSystemLoader(str(p)) for p in (host_template_dirs or [])]
    loaders.append(PackageLoader(_PACKAGE, _TEMPLATE_DIR))
    return Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def build_ui_router(rs: RegStack) -> APIRouter:
    """Server-rendered pages that pair with the JSON API.

    Pages are stateless: they read API + UI prefixes from the page body and
    let ``regstack.js`` drive form submissions and auth-state redirects.
    No cookie-based session is established here, so this router is safe to
    mount alongside the JSON API without CSRF middleware.
    """
    router = APIRouter()
    env = rs.ui_env

    def _render(template_name: str, page: str, **extra: object) -> HTMLResponse:
        ctx = _base_context(rs, page=page)
        ctx.update(extra)
        body = env.get_template(template_name).render(ctx)
        return HTMLResponse(body)

    @router.get("/login", response_class=HTMLResponse, summary="Sign-in page")
    async def login_page(_request: Request) -> HTMLResponse:
        return _render("auth/login.html", page="login")

    @router.get("/register", response_class=HTMLResponse, summary="Account creation page")
    async def register_page(_request: Request) -> HTMLResponse:
        return _render("auth/register.html", page="register")

    @router.get(
        "/forgot",
        response_class=HTMLResponse,
        summary="Forgot-password request page",
        include_in_schema=rs.config.enable_password_reset,
    )
    async def forgot_page(_request: Request) -> HTMLResponse:
        return _render("auth/forgot.html", page="forgot")

    @router.get(
        "/reset",
        response_class=HTMLResponse,
        summary="Set a new password (token comes from query string)",
        include_in_schema=rs.config.enable_password_reset,
    )
    async def reset_page(_request: Request) -> HTMLResponse:
        return _render("auth/reset.html", page="reset")

    @router.get(
        "/verify",
        response_class=HTMLResponse,
        summary="Auto-confirms a verification token from the query string",
    )
    async def verify_page(_request: Request) -> HTMLResponse:
        return _render("auth/verify.html", page="verify")

    @router.get(
        "/confirm-email-change",
        response_class=HTMLResponse,
        summary="Auto-confirms an email-change token from the query string",
    )
    async def confirm_email_change_page(_request: Request) -> HTMLResponse:
        return _render(
            "auth/email_change_confirm.html",
            page="confirm-email-change",
        )

    @router.get(
        "/me",
        response_class=HTMLResponse,
        summary="Authenticated account dashboard (client-side gate)",
    )
    async def me_page(_request: Request) -> HTMLResponse:
        return _render("auth/me.html", page="me")

    @router.get(
        "/mfa-confirm",
        response_class=HTMLResponse,
        summary="Second step of an MFA-required sign-in",
        include_in_schema=rs.config.enable_sms_2fa,
    )
    async def mfa_confirm_page(_request: Request) -> HTMLResponse:
        return _render("auth/mfa_confirm.html", page="mfa-confirm")

    return router


def _base_context(rs: RegStack, *, page: str) -> dict[str, object]:
    return {
        "page": page,
        "app_name": rs.config.app_name,
        "brand_logo_url": rs.config.brand_logo_url,
        "brand_tagline": rs.config.brand_tagline,
        "api_prefix": rs.config.api_prefix.rstrip("/"),
        "ui_prefix": rs.config.ui_prefix.rstrip("/"),
        "static_prefix": rs.config.static_prefix.rstrip("/"),
        "theme_css_url": rs.config.theme_css_url,
        "allow_registration": rs.config.allow_registration,
        "enable_password_reset": rs.config.enable_password_reset,
        "enable_account_deletion": rs.config.enable_account_deletion,
        "enable_sms_2fa": rs.config.enable_sms_2fa,
    }


__all__ = ["PAGE_NAMES", "build_ui_environment", "build_ui_router", "default_static_dir"]
