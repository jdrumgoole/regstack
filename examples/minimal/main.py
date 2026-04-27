"""Minimal embedding example (MongoDB).

Run from the repo root:

    uv run uvicorn examples.minimal.main:app --reload

Or with a custom config:

    REGSTACK_CONFIG=examples/minimal/regstack.toml \\
    REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))') \\
    REGSTACK_DATABASE_URL=mongodb://localhost:27017/regstack_demo \\
    uv run uvicorn examples.minimal.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from regstack import RegStack, RegStackConfig


def make_config() -> RegStackConfig:
    here = Path(__file__).parent
    return RegStackConfig.load(toml_path=here / "regstack.toml")


config = make_config()
regstack = RegStack(config=config)


# Demo-only hooks: print the verification / reset URLs to stdout so a curl-driven
# walkthrough can grab them without scraping logs. Real hosts wouldn't do this.
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


regstack.on("verification_requested", _print_verification)
regstack.on("password_reset_requested", _print_reset)
regstack.on("email_change_requested", _print_email_change)
regstack.on("phone_setup_started", _print_phone_setup)
regstack.on("mfa_login_started", _print_login_mfa)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await regstack.install_schema()
    yield
    await regstack.aclose()


app = FastAPI(title=f"{config.app_name} (regstack demo)", lifespan=lifespan)
app.include_router(regstack.router, prefix=config.api_prefix)
if config.enable_ui_router:
    app.include_router(regstack.ui_router, prefix=config.ui_prefix)
    app.mount(config.static_prefix, regstack.static_files)
    # Optional host theme override — mount a local "branding/" dir so the
    # demo can show a host-supplied theme.css under /branding/theme.css.
    branding_dir = Path(__file__).parent / "branding"
    if branding_dir.is_dir():
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
    return {"app": config.app_name, "regstack_routes": routes}
