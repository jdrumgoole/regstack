"""Shared FastAPI scaffold used by every per-backend demo.

Each `examples/<backend>/main.py` builds a `RegStackConfig` from its own
`regstack.toml`, instantiates a `RegStack`, and hands both off to
``build_demo_app`` here. The only thing that differs between demos is
the database URL; the routes, hooks, and theme are identical.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from regstack import RegStack, RegStackConfig


# Demo-only printer hooks: write magic-link / SMS-code values to stdout
# so a curl-driven walkthrough can grab them. Real hosts wouldn't do
# this — they'd hand the events off to a notification subsystem.
async def _print_verification(email: str, url: str) -> None:
    print(f"\n[demo] verification link for {email}:\n  {url}\n", flush=True)


async def _print_reset(user, url: str) -> None:
    print(f"\n[demo] password-reset link for {user.email}:\n  {url}\n", flush=True)


async def _print_email_change(user, new_email: str, url: str) -> None:
    print(
        f"\n[demo] email-change confirmation for {user.email} -> {new_email}:\n  {url}\n",
        flush=True,
    )


async def _print_phone_setup(user, phone: str, code: str) -> None:
    print(f"\n[demo] phone-setup code for {user.email} -> {phone}: {code}\n", flush=True)


async def _print_login_mfa(user, code: str) -> None:
    print(f"\n[demo] sign-in code for {user.email}: {code}\n", flush=True)


def attach_demo_hooks(regstack: RegStack) -> None:
    regstack.on("verification_requested", _print_verification)
    regstack.on("password_reset_requested", _print_reset)
    regstack.on("email_change_requested", _print_email_change)
    regstack.on("phone_setup_started", _print_phone_setup)
    regstack.on("mfa_login_started", _print_login_mfa)


def build_demo_app(
    *,
    config: RegStackConfig,
    regstack: RegStack,
    branding_dir: Path | None = None,
    title_suffix: str = "regstack demo",
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await regstack.install_schema()
        yield
        await regstack.aclose()

    app = FastAPI(title=f"{config.app_name} ({title_suffix})", lifespan=lifespan)
    app.include_router(regstack.router, prefix=config.api_prefix)
    if config.enable_ui_router:
        app.include_router(regstack.ui_router, prefix=config.ui_prefix)
        app.mount(config.static_prefix, regstack.static_files)
        if branding_dir and branding_dir.is_dir():
            app.mount("/branding", StaticFiles(directory=str(branding_dir)))

    @app.get("/")
    async def root() -> dict[str, list[str] | str]:
        routes = [
            "register",
            "verify",
            "resend-verification",
            "login",
            "logout",
            "me",
            "change-password",
            "change-email",
            "confirm-email-change",
            "account",
        ]
        if config.enable_password_reset:
            routes.extend(["forgot-password", "reset-password"])
        if config.enable_sms_2fa:
            routes.extend(["phone/start", "phone/confirm", "login/mfa-confirm"])
        if config.enable_admin_router:
            routes.extend(["admin/stats", "admin/users", "admin/users/{id}"])
        return {
            "app": config.app_name,
            "backend": regstack.backend.kind,
            "regstack_routes": routes,
        }

    return app
