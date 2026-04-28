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
    """Return the filesystem path to the bundled static assets.

    Resolves ``regstack/ui/static/`` from the installed package via
    ``importlib.resources``, so it works whether regstack is in an
    editable install or a wheel. Used by the ``StaticFiles`` factory
    on :class:`~regstack.app.RegStack`.

    Returns:
        Filesystem path containing ``css/core.css``, ``css/theme.css``,
        and ``js/regstack.js``.
    """
    return Path(str(resources.files(_PACKAGE).joinpath(_STATIC_DIR)))


def build_ui_environment(host_template_dirs: list[Path] | None = None) -> Environment:
    """Construct the Jinja2 environment used by the SSR pages.

    Wraps a :class:`jinja2.ChoiceLoader` so that **host directories
    are searched first**, falling back to the bundled templates from
    the regstack package. A host can override ``auth/login.html``
    (or any other bundled template) by dropping a same-named file
    into one of the supplied directories.

    Args:
        host_template_dirs: Optional list of host template directories
            to prepend. Order matters — earlier entries win on
            collisions.

    Returns:
        A configured :class:`jinja2.Environment` with autoescape on
        for HTML.
    """
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
    """Build the SSR :class:`APIRouter` for the bundled HTML pages.

    Mounts a ``GET`` endpoint for each of :data:`PAGE_NAMES`
    (``login``, ``register``, ``verify``, ``forgot``, ``reset``,
    ``me``, ``confirm-email-change``, ``mfa-confirm``).

    Pages are **stateless** — they render the same HTML regardless of
    auth state. The bundled ``regstack.js`` reads the API and UI
    prefixes from ``<body data-rs-api data-rs-ui>``, drives form
    submissions via ``fetch``, and stores the access token in
    ``localStorage``. No cookie session is established here, so this
    router is safe to mount alongside the JSON API without CSRF
    middleware.

    Args:
        rs: The owning :class:`~regstack.app.RegStack` instance — its
            config drives the page context (brand, prefixes, theme
            URL) and its template environment is reused.

    Returns:
        A FastAPI ``APIRouter`` ready for ``app.include_router(...,
        prefix=config.ui_prefix)``.
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
