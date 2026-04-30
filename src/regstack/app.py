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
    from regstack.oauth import OAuthRegistry


class RegStack:
    """Embeddable account-management façade.

    One ``RegStack`` is constructed per FastAPI application. The host
    then mounts the JSON router (and optionally the SSR router) and
    regstack owns user accounts, authentication, password reset, email
    verification, and (optionally) SMS two-factor.

    The persistence story is owned by a
    :class:`~regstack.backends.base.Backend` selected by
    ``config.database_url``'s URL scheme:

    - ``mongodb://`` / ``mongodb+srv://`` → MongoDB
    - ``sqlite+aiosqlite://`` → SQLite
    - ``postgresql+asyncpg://`` → PostgreSQL

    Hosts that need to share a connection pool with their own code can
    pass an explicit ``backend=`` argument and the URL is ignored.

    Typical embed::

        config = RegStackConfig.load()
        regstack = RegStack(config=config)

        @asynccontextmanager
        async def lifespan(app):
            await regstack.install_schema()
            yield
            await regstack.aclose()

        app = FastAPI(lifespan=lifespan)
        app.include_router(regstack.router, prefix=config.api_prefix)

    Notable instance attributes (all set during ``__init__``):

    - ``config`` — the loaded :class:`~regstack.config.schema.RegStackConfig`.
    - ``clock`` — the injected :class:`~regstack.auth.clock.Clock`
      (``SystemClock`` in production, ``FrozenClock`` in tests).
    - ``backend`` — the active backend (Mongo / SQLite / Postgres).
    - ``users``, ``pending``, ``blacklist``, ``attempts``, ``mfa_codes``
      — repositories conforming to the protocols in
      :mod:`regstack.backends.protocols`.
    - ``password_hasher`` — Argon2id wrapper.
    - ``jwt`` — :class:`~regstack.auth.jwt.JwtCodec` for session tokens.
    - ``lockout`` — :class:`~regstack.auth.lockout.LockoutService`.
    - ``email``, ``sms`` — the active transports.
    - ``mail`` — the :class:`~regstack.email.composer.MailComposer`.
    - ``hooks`` — the :class:`~regstack.hooks.events.HookRegistry`
      event bus.
    - ``deps`` — :class:`~regstack.auth.dependencies.AuthDependencies`
      factory.
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
        """Construct the façade and wire its collaborators.

        Args:
            config: Loaded configuration (see
                :func:`~regstack.config.schema.RegStackConfig.load`).
            backend: Optional pre-built backend. When ``None``, the
                backend is built from ``config.database_url`` via
                :func:`~regstack.backends.factory.build_backend`. Pass an
                explicit backend if you want to share a connection pool
                with the host application.
            clock: Optional clock. Defaults to
                :class:`~regstack.auth.clock.SystemClock`. Tests pass a
                ``FrozenClock`` to make timing-sensitive assertions
                deterministic.
            email_service: Optional pre-built email backend. When
                ``None``, one is built from ``config.email`` via
                :func:`~regstack.email.factory.build_email_service`.
            mail_composer: Optional pre-built mail composer. When
                ``None``, one is built from ``config.email`` and
                ``config.app_name``.
            sms_service: Optional pre-built SMS backend. When ``None``,
                one is built from ``config.sms`` via
                :func:`~regstack.sms.factory.build_sms_service`.
        """
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
        self.oauth_identities = self.backend.oauth_identities
        self.oauth_states = self.backend.oauth_states

        self.lockout = LockoutService(attempts=self.attempts, config=config, clock=self.clock)
        self.email: EmailService = email_service or build_email_service(config.email)
        self.sms: SmsService = sms_service or build_sms_service(config.sms)
        self.mail = mail_composer or MailComposer(
            email_config=config.email,
            app_name=config.app_name,
        )
        self.hooks = HookRegistry()
        self.deps = AuthDependencies(jwt=self.jwt, users=self.users, blacklist=self.blacklist)
        self.oauth = self._build_oauth_registry()
        self._template_dirs: list[Path] = list(config.extra_template_dirs)
        self._ui_env: Environment | None = None
        self._router: APIRouter | None = None
        self._ui_router: APIRouter | None = None
        self._static_files: StaticFiles | None = None

    def _build_oauth_registry(self) -> OAuthRegistry:
        """Build the OAuth registry, populated from config.

        The ``regstack.oauth`` import is lazy so the package keeps
        importing on a base install (no ``oauth`` extra). When
        ``enable_oauth`` is off the registry is empty; the router won't
        be mounted regardless.
        """
        from regstack.oauth import OAuthRegistry

        registry = OAuthRegistry()
        if not self.config.enable_oauth:
            return registry
        oauth_cfg = self.config.oauth
        if oauth_cfg.google_client_id and oauth_cfg.google_client_secret:
            from regstack.oauth.providers.google import GoogleProvider

            registry.register(
                GoogleProvider(
                    client_id=oauth_cfg.google_client_id,
                    client_secret=oauth_cfg.google_client_secret.get_secret_value(),
                )
            )
        return registry

    @property
    def router(self) -> APIRouter:
        """The composite JSON ``APIRouter``.

        Mount with
        ``app.include_router(regstack.router, prefix=config.api_prefix)``.
        Includes ``register``, ``verify``, ``login``, ``logout``,
        ``account`` always; conditionally adds ``password``
        (forgot/reset), ``phone`` + MFA, and ``admin`` based on
        ``config.enable_*`` flags.

        Built lazily on first access.
        """
        if self._router is None:
            self._router = build_router(self)
        return self._router

    @property
    def ui_env(self) -> Environment:
        """The Jinja2 environment that renders the SSR pages.

        Built lazily on first access; rebuilt automatically after every
        :meth:`add_template_dir` call so host overrides take effect.
        """
        if self._ui_env is None:
            self._ui_env = build_ui_environment(self._template_dirs)
        return self._ui_env

    @property
    def ui_router(self) -> APIRouter:
        """The SSR ``APIRouter`` for the bundled HTML pages.

        Mount with ``app.include_router(regstack.ui_router,
        prefix=config.ui_prefix)``. Only meaningful when
        ``config.enable_ui_router=True`` — building it on a host that
        won't mount it is harmless but pointless.
        """
        if self._ui_router is None:
            self._ui_router = build_ui_router(self)
        return self._ui_router

    @property
    def static_files(self) -> StaticFiles:
        """Bundled CSS / JS as a Starlette ``StaticFiles`` app.

        Mount with
        ``app.mount(config.static_prefix, regstack.static_files)``.
        Serves ``core.css``, the default ``theme.css``, and
        ``regstack.js`` — the assets the SSR pages link to.
        """
        if self._static_files is None:
            self._static_files = StaticFiles(directory=str(default_static_dir()))
        return self._static_files

    # --- Lifecycle -------------------------------------------------------

    async def install_schema(self) -> None:
        """Bring the database schema to head — idempotent.

        On Mongo this means ensuring every required index exists. On
        SQL backends it runs Alembic migrations to head, which creates
        tables on a fresh database and applies any new revisions on an
        existing one. Safe to call on every application boot.
        """
        await self.backend.install_schema()

    async def aclose(self) -> None:
        """Tear down the backend's connection pool.

        Call from your FastAPI lifespan teardown so background
        connections are closed cleanly when the application shuts down.
        """
        await self.backend.aclose()

    async def bootstrap_admin(self, email: str, password: str) -> BaseUser:
        """Create or promote a verified superuser. Idempotent.

        If a user with this email already exists, they are promoted to
        ``is_superuser=True`` if they weren't already (their password is
        not changed). Otherwise a new active, verified, superuser
        account is created with the given password.

        Args:
            email: The admin's email address. Must be valid for the
                user model's ``email`` validator.
            password: The plaintext password to hash and store on a
                newly-created admin. Ignored when promoting an existing
                user.

        Returns:
            The persisted (and now-superuser) :class:`~regstack.models.user.BaseUser`.

        Raises:
            UserAlreadyExistsError: If the create path races against
                another writer for the same email.
        """
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
        """Replace the active email backend at runtime.

        Useful for hosts that want a backend not in the bundled set
        (Postmark, SendGrid, MessageBird, …). See
        :class:`~regstack.email.base.EmailService` for the contract.

        Args:
            service: An :class:`EmailService` implementation.
        """
        self.email = service

    def set_sms_backend(self, service: SmsService) -> None:
        """Replace the active SMS backend at runtime.

        Args:
            service: A :class:`~regstack.sms.base.SmsService` implementation.
        """
        self.sms = service

    def add_template_dir(self, path: str | Path) -> None:
        """Prepend a host template directory to the override chain.

        Host templates win over regstack defaults via Jinja2's
        ``ChoiceLoader`` for **both** the email composer and the SSR UI
        pages. To override the verification email, drop a
        ``verification.html`` file in the directory; to override the
        login page, drop ``auth/login.html``.

        Args:
            path: Filesystem directory to search before regstack's
                bundled templates. Must exist when templates are
                rendered.
        """
        path_obj = Path(path)
        self.mail.add_template_dir(path_obj)
        if path_obj not in self._template_dirs:
            self._template_dirs.insert(0, path_obj)
        # Force the UI environment to rebuild on next access so the new
        # directory takes effect even if the env was already touched.
        self._ui_env = None

    def on(self, event: str, handler: Callable[..., Awaitable[None] | None]) -> None:
        """Register an event handler. Sync and async handlers both work.

        Forwards to :meth:`HookRegistry.on
        <regstack.hooks.events.HookRegistry.on>`. Handlers fire
        concurrently when an event happens; exceptions are logged but
        never break the primary auth flow. See
        :data:`~regstack.hooks.events.KNOWN_EVENTS` for the set of
        events regstack itself fires.

        Args:
            event: The event name (e.g. ``"user_registered"``,
                ``"password_changed"``).
            handler: A callable invoked with the event's keyword
                arguments. Can be sync or async.
        """
        self.hooks.on(event, handler)
