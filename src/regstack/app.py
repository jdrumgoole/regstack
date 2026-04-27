from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.staticfiles import StaticFiles

from regstack.auth.clock import Clock, SystemClock
from regstack.auth.dependencies import AuthDependencies
from regstack.auth.jwt import JwtCodec
from regstack.auth.lockout import LockoutService
from regstack.auth.password import PasswordHasher
from regstack.backends.factory import build_backend
from regstack.config.schema import RegStackConfig
from regstack.email.base import EmailService
from regstack.email.composer import MailComposer
from regstack.email.factory import build_email_service
from regstack.hooks.events import HookRegistry
from regstack.models.user import BaseUser
from regstack.routers import build_router
from regstack.sms.base import SmsService
from regstack.sms.factory import build_sms_service
from regstack.ui.pages import build_ui_environment, build_ui_router, default_static_dir

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import APIRouter
    from jinja2 import Environment

    from regstack.backends.base import Backend


class RegStack:
    """Embeddable account-management module.

    Hosts construct one of these per FastAPI application, then mount
    ``regstack.router`` with ``app.include_router(regstack.router, prefix=...)``.

    The persistence story is owned by a :class:`~regstack.backends.base.Backend`
    selected by ``config.database_url``'s URL scheme — `mongodb://` →
    Mongo, `sqlite+aiosqlite://` → SQLite, `postgresql+asyncpg://` →
    Postgres. Hosts that need to share a connection pool with their own
    code can pass an explicit ``backend=`` argument.
    """

    def __init__(
        self,
        *,
        config: RegStackConfig,
        backend: Backend | None = None,
        clock: Clock | None = None,
        email_service: EmailService | None = None,
        mail_composer: MailComposer | None = None,
        sms_service: SmsService | None = None,
    ) -> None:
        self.config = config
        self.clock: Clock = clock or SystemClock()
        self.backend: Backend = backend or build_backend(config, clock=self.clock)
        self.password_hasher = PasswordHasher()
        self.jwt = JwtCodec(config, self.clock)

        # Repos come straight off the backend so they're always in sync
        # with whatever implementation is configured.
        self.users = self.backend.users
        self.pending = self.backend.pending
        self.blacklist = self.backend.blacklist
        self.attempts = self.backend.attempts
        self.mfa_codes = self.backend.mfa_codes

        self.lockout = LockoutService(attempts=self.attempts, config=config, clock=self.clock)
        self.email: EmailService = email_service or build_email_service(config.email)
        self.sms: SmsService = sms_service or build_sms_service(config.sms)
        self.mail = mail_composer or MailComposer(
            email_config=config.email,
            app_name=config.app_name,
        )
        self.hooks = HookRegistry()
        self.deps = AuthDependencies(jwt=self.jwt, users=self.users, blacklist=self.blacklist)
        self._template_dirs: list[Path] = list(config.extra_template_dirs)
        self._ui_env: Environment | None = None
        self._router: APIRouter | None = None
        self._ui_router: APIRouter | None = None
        self._static_files: StaticFiles | None = None

    @property
    def router(self) -> APIRouter:
        if self._router is None:
            self._router = build_router(self)
        return self._router

    @property
    def ui_env(self) -> Environment:
        if self._ui_env is None:
            self._ui_env = build_ui_environment(self._template_dirs)
        return self._ui_env

    @property
    def ui_router(self) -> APIRouter:
        if self._ui_router is None:
            self._ui_router = build_ui_router(self)
        return self._ui_router

    @property
    def static_files(self) -> StaticFiles:
        """Bundled CSS / JS — host mounts at ``config.static_prefix``."""
        if self._static_files is None:
            self._static_files = StaticFiles(directory=str(default_static_dir()))
        return self._static_files

    # --- Lifecycle -------------------------------------------------------

    async def install_schema(self) -> None:
        """Create indexes (Mongo) or run migrations (SQL). Idempotent."""
        await self.backend.install_schema()

    # Legacy alias kept for the 0.1.x install_indexes() name. New callers
    # should use install_schema().
    async def install_indexes(self) -> None:
        await self.install_schema()

    async def aclose(self) -> None:
        """Tear down the backend's connection pool."""
        await self.backend.aclose()

    async def bootstrap_admin(self, email: str, password: str) -> BaseUser:
        """Create a verified superuser if none exists for the given email."""
        existing = await self.users.get_by_email(email)
        if existing is not None:
            if not existing.is_superuser:
                assert existing.id is not None
                await self.users.set_superuser(existing.id, is_superuser=True)
                existing.is_superuser = True
            return existing
        user = BaseUser(
            email=email,
            hashed_password=self.password_hasher.hash(password),
            is_active=True,
            is_verified=True,
            is_superuser=True,
        )
        return await self.users.create(user)

    # --- Extension surface ------------------------------------------------

    def set_email_backend(self, service: EmailService) -> None:
        self.email = service

    def set_sms_backend(self, service: SmsService) -> None:
        self.sms = service

    def add_template_dir(self, path: str | Path) -> None:
        """Prepend a host-supplied template directory. Host templates win
        over regstack defaults via Jinja2's ``ChoiceLoader`` for both the
        email composer and the SSR UI pages.
        """
        path_obj = Path(path)
        self.mail.add_template_dir(path_obj)
        if path_obj not in self._template_dirs:
            self._template_dirs.insert(0, path_obj)
        # Force the UI environment to rebuild on next access so the new
        # directory takes effect even if the env was already touched.
        self._ui_env = None

    def on(self, event: str, handler: Callable[..., Awaitable[None] | None]) -> None:
        self.hooks.on(event, handler)
