from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

EmailBackend = Literal["console", "smtp", "ses"]
SmsBackend = Literal["null", "sns", "twilio"]
JwtAlgorithm = Literal["HS256", "HS384", "HS512"]
TokenTransport = Literal["bearer"]
# Note: a "cookie" transport was previously listed here but never
# implemented — `AuthDependencies._authenticate` reads `HTTPBearer`
# tokens unconditionally and no router sets a `Set-Cookie` header.
# Hosts that set `transport = "cookie"` were silently no-op'd, which
# is a security misconfiguration risk (the host thinks tokens are
# cookies, the library expects Authorization headers). Tightening the
# Literal makes the misconfiguration surface as a config validation
# error instead. If cookie transport ships later, add it back here.
# Flagged as I-7 in the 2026-05-15 / 2026-05-16 security reviews.


class EmailConfig(BaseModel):
    backend: EmailBackend = "console"
    from_address: EmailStr = "noreply@example.com"
    from_name: str | None = None
    """Display name on the ``From:`` header. ``None`` (the default) defers
    to :attr:`RegStackConfig.app_name`, so changing only ``app_name`` is
    enough to brand the outgoing emails. Set explicitly to override —
    e.g. ``from_name = "Acme Customer Service"`` when ``app_name`` is the
    internal product code and shouldn't appear to end-users."""

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

    ses_region: str = "eu-west-1"
    ses_profile: str | None = None
    ses_access_key_id: SecretStr | None = None
    """Explicit AWS access key id for the SES backend. ``None`` (the
    default) lets boto3 walk its usual credential chain — env vars,
    shared credentials file, EC2/ECS instance profile. Set explicitly
    when the host already loads its AWS credentials from a secrets
    store (Vault, AWS Secrets Manager, ``secrets.env``) and would
    rather pass them in than re-export them into the process env."""
    ses_secret_access_key: SecretStr | None = None
    """Explicit AWS secret access key — pairs with
    :attr:`ses_access_key_id`. Both must be set together; setting only
    one is a config error."""

    log_bodies: bool = False
    """When True, the console backend logs the full rendered body at
    INFO instead of DEBUG. Off by default because bodies contain
    one-time tokens; turn on only for the ``console`` backend in a
    dev/staging deployment that you intend to probe with
    ``regstack validate``. Other backends ignore this flag — they
    don't log bodies at all."""

    @model_validator(mode="after")
    def _validate_ses_creds(self) -> EmailConfig:
        explicit_key = self.ses_access_key_id is not None
        explicit_secret = self.ses_secret_access_key is not None
        if explicit_key != explicit_secret:
            raise ValueError(
                "ses_access_key_id and ses_secret_access_key must be set "
                "together (or both left unset to use boto3's default "
                "credential chain)."
            )
        if (explicit_key or explicit_secret) and self.ses_profile is not None:
            # boto3 lets profile + explicit creds coexist but the resolution
            # order is surprising — explicit creds win silently. Reject the
            # combination so the host can't think they're using one when
            # they're actually using the other.
            raise ValueError(
                "ses_profile and explicit ses_access_key_id/"
                "ses_secret_access_key are mutually exclusive. Pick one."
            )
        return self


class SmsConfig(BaseModel):
    backend: SmsBackend = "null"
    from_number: str | None = None

    sns_region: str = "eu-west-1"

    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None

    log_bodies: bool = False
    """When True, the ``null`` backend logs the SMS body (including the
    6-digit MFA code) at INFO. Defaults to False so a misconfigured
    deployment can't leak codes into shared logs — symmetric with
    ``email.log_bodies``. Set it True for local dev when you want to
    read the code out of stdout (the bundled examples instead surface
    it via an ``mfa_login_started`` hook, so they don't need this).
    Other backends ignore this flag — they never log message bodies.
    (Security review 2026-05-19.)"""


class OAuthConfig(BaseModel):
    """OAuth provider configuration.

    The router is mounted only when ``enable_oauth=True`` AND at least
    one provider has its credentials set (currently just Google).
    """

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: AnyHttpUrl | None = None
    """Defaults to ``f"{base_url}{api_prefix}/oauth/google/callback"``
    when None. Must match the redirect URI registered with the
    provider exactly."""

    auto_link_verified_emails: bool = False
    """Auto-link Google sign-ins to existing email-registered users
    when the provider's ``email_verified=true``. **Off by default**:
    that policy trusts the provider's verified-email claim *forever*,
    which lets account-recycling at the provider become an account
    takeover at the host. Hosts that consciously accept that risk for
    UX can flip this on; the safer default is to require an
    authenticated link from /account/me. See tasks/oauth-design.md §1
    for the full threat model."""

    enforce_mfa_on_oauth_signin: bool = False
    """When True, an OAuth sign-in for a user with SMS MFA enabled
    still goes through the second-factor step. Off by default — the
    OAuth provider already authenticated the human, and SMS over
    OAuth is mostly redundant outside regulated environments."""

    state_ttl_seconds: int = 300
    completion_ttl_seconds: int = 30


class RegStackConfig(BaseSettings):
    """Top-level configuration for an embedded regstack instance.

    Loading order (highest priority first):
        1. Programmatic kwargs.
        2. Environment variables (``REGSTACK_*``, nested via ``__``).
        3. TOML file at ``$REGSTACK_CONFIG`` or ``./regstack.toml``.
        4. Field defaults defined here.
    """

    model_config = SettingsConfigDict(
        env_prefix="REGSTACK_",
        env_nested_delimiter="__",
        extra="ignore",
        populate_by_name=True,
    )

    # Identity / hosting
    app_name: str = "RegStack"
    base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    behind_proxy: bool = False

    # Database
    # Backend is selected by URL scheme. Supported:
    #   sqlite+aiosqlite:///./regstack.db        — SQLite (default; zero infra)
    #   postgresql+asyncpg://user:pw@host/db     — Postgres
    #   mongodb://host:port/dbname               — MongoDB
    database_url: SecretStr = SecretStr("sqlite+aiosqlite:///./regstack.db")
    # Mongo-only fallback for when the URL has no /dbname path.
    mongodb_database: str = "regstack"
    # Collection / table names (used by the active backend).
    user_collection: str = "users"
    pending_collection: str = "pending_registrations"
    blacklist_collection: str = "token_blacklist"
    login_attempt_collection: str = "login_attempts"
    mfa_code_collection: str = "mfa_codes"
    oauth_identity_collection: str = "oauth_identities"
    oauth_state_collection: str = "oauth_states"

    # JWT
    jwt_secret: SecretStr = Field(default_factory=lambda: SecretStr(""))
    jwt_algorithm: JwtAlgorithm = "HS256"
    jwt_ttl_seconds: Annotated[int, Field(ge=60, le=60 * 60 * 24 * 30)] = 7200
    jwt_audience: str | None = None
    transport: TokenTransport = "bearer"

    # Verification & password-reset & email-change token lifetimes
    verification_token_ttl_seconds: Annotated[int, Field(ge=60)] = 60 * 60 * 24
    password_reset_token_ttl_seconds: Annotated[int, Field(ge=60)] = 60 * 30
    email_change_token_ttl_seconds: Annotated[int, Field(ge=60)] = 60 * 60

    # SMS / 2FA
    sms_code_length: Annotated[int, Field(ge=4, le=10)] = 6
    sms_code_ttl_seconds: Annotated[int, Field(ge=30, le=60 * 30)] = 300
    sms_code_max_attempts: Annotated[int, Field(ge=1, le=20)] = 5
    mfa_pending_token_ttl_seconds: Annotated[int, Field(ge=60, le=60 * 30)] = 600

    # Feature flags
    require_verification: bool = True
    allow_registration: bool = True
    enable_password_reset: bool = True
    enable_account_deletion: bool = True
    enable_admin_router: bool = False
    enable_ui_router: bool = False
    enable_sms_2fa: bool = False
    enable_oauth: bool = False  # reserved; no providers ship in v1

    # Login lockout (M2: count failed attempts per email in a sliding window)
    rate_limit_disabled: bool = False
    login_lockout_threshold: Annotated[int, Field(ge=1)] = 5
    login_lockout_window_seconds: Annotated[int, Field(ge=10)] = 900

    # Per-route IP-based rate limits, slowapi syntax (``"5/minute"`` or
    # composite ``"5/minute;20/hour"``). Independent of the per-account
    # lockout — those are about credential-stuffing one account, these are
    # about a single source IP hammering an endpoint. None means "no
    # limit on this route". The Limiter is either host-supplied via
    # ``RegStack(rate_limiter=...)`` or built from the ``rate_limit`` extra.
    login_rate_limit: str | None = None
    login_mfa_confirm_rate_limit: str | None = None
    register_rate_limit: str | None = None
    forgot_password_rate_limit: str | None = None
    reset_password_rate_limit: str | None = None
    verify_rate_limit: str | None = None
    resend_verification_rate_limit: str | None = None
    change_password_rate_limit: str | None = None
    change_email_rate_limit: str | None = None
    confirm_email_change_rate_limit: str | None = None
    delete_account_rate_limit: str | None = None
    oauth_exchange_rate_limit: str | None = None
    # Phone routes send paid SMS and brute-force a 6-digit code, so
    # they need IP throttles regardless of the per-code attempt counter
    # in mfa_codes. (Review #6.)
    phone_start_rate_limit: str | None = None
    phone_confirm_rate_limit: str | None = None
    phone_disable_rate_limit: str | None = None

    # Sub-configs
    email: EmailConfig = Field(default_factory=EmailConfig)
    sms: SmsConfig = Field(default_factory=SmsConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)

    # Branding / theming
    brand_logo_url: str | None = None
    brand_tagline: str | None = None
    extra_template_dirs: list[Path] = Field(default_factory=list)
    extra_static_dirs: list[Path] = Field(default_factory=list)

    # SSR ui_router URLs
    api_prefix: str = "/api/auth"
    ui_prefix: str = "/account"
    static_prefix: str = "/regstack-static"
    theme_css_url: str | None = None  # if set, loaded AFTER bundled defaults

    # Path prefix prepended to the bare /verify, /reset-password and
    # /confirm-email-change paths used in verification, password-reset and
    # email-change emails. ``None`` (default) auto-resolves: when the bundled
    # UI router is enabled the resolved value is ``ui_prefix`` (so links land
    # on regstack's themed pages); otherwise it resolves to ``""`` so the
    # host application can intercept the bare paths itself (matches pre-0.7
    # behaviour for SPA hosts). Set explicitly to override either default —
    # e.g. ``email_link_prefix = "/my-app"`` if your SPA owns the auth pages
    # under a different mount.
    email_link_prefix: str | None = None

    # Full URL templates for the three transactional email links. When set,
    # bypass ``email_link_prefix`` entirely and use the template verbatim
    # with ``{base_url}`` and ``{token}`` substituted in. Designed for SPA
    # hosts whose router doesn't take the canonical
    # ``/verify?token=...`` / ``/reset-password?token=...`` shape (e.g.
    # hash routing: ``{base_url}/#/verify/{token}``) or hosts whose auth
    # pages live on a different subdomain (``https://app.example.com/...``
    # vs ``base_url``).
    #
    # ``{base_url}`` substitutes the config's ``base_url`` with the trailing
    # slash trimmed. ``{token}`` substitutes the raw verification /
    # password-reset / email-change token. Both substitutions are exact
    # string replacement — no URL-encoding is applied so the host can
    # decide the encoding for its router. Default: ``None`` (preserves
    # the prefix-based composition used by every release through 0.6.0).
    verify_url_template: str | None = None
    password_reset_url_template: str | None = None
    email_change_url_template: str | None = None

    @field_validator("jwt_secret")
    @classmethod
    def _warn_empty_secret(cls, v: SecretStr) -> SecretStr:
        # An empty or short secret is allowed at construction time so defaults
        # remain usable in tests; production callers should populate it
        # explicitly or via the wizard. Both the non-empty and the
        # >= MIN_JWT_SECRET_LENGTH requirements are enforced where the secret
        # is first used to sign — JwtCodec.__init__, reached via the RegStack
        # façade — so test fixtures can build a bare config and opt out.
        return v

    def resolve_email_link_prefix(self) -> str:
        """Return the path prefix to prepend to email-link paths.

        Used by the verification, password-reset and email-change routers
        when composing the URLs embedded in outgoing emails. See the
        ``email_link_prefix`` field for the semantics.
        """
        if self.email_link_prefix is not None:
            return self.email_link_prefix.rstrip("/")
        if self.enable_ui_router:
            return self.ui_prefix.rstrip("/")
        return ""

    def _compose_email_url(
        self,
        *,
        template: str | None,
        bare_path: str,
        token: str,
    ) -> str:
        """Build a transactional-email URL, preferring the host-supplied
        template over the prefix-based composition.

        Both substitutions are deliberately literal — no URL-encoding —
        so a host that wants ``{token}`` in a path segment can drop the
        ``?token=`` query string entirely without regstack pre-encoding
        the value as if it were one.

        Uses ``str.replace`` rather than ``str.format`` deliberately:
        ``str.format`` would accept attribute-traversal syntax like
        ``{base_url.__class__.__name__}`` and would raise ``KeyError``
        at first email-send time on a typoed placeholder (surfacing as
        a 500 on register). ``str.replace`` leaves unrecognised
        ``{whatever}`` strings in place so the operator gets a visibly
        wrong link in the email instead of a 500 during signup.
        """
        base = str(self.base_url).rstrip("/")
        if template is not None:
            return template.replace("{base_url}", base).replace("{token}", token)
        prefix = self.resolve_email_link_prefix()
        return f"{base}{prefix}{bare_path}?token={token}"

    def resolve_verify_url(self, token: str) -> str:
        """Return the URL embedded in the verification email."""
        return self._compose_email_url(
            template=self.verify_url_template,
            bare_path="/verify",
            token=token,
        )

    def resolve_password_reset_url(self, token: str) -> str:
        """Return the URL embedded in the password-reset email."""
        return self._compose_email_url(
            template=self.password_reset_url_template,
            bare_path="/reset-password",
            token=token,
        )

    def resolve_email_change_url(self, token: str) -> str:
        """Return the URL embedded in the email-change confirmation email."""
        return self._compose_email_url(
            template=self.email_change_url_template,
            bare_path="/confirm-email-change",
            token=token,
        )

    @classmethod
    def load(
        cls,
        toml_path: Path | str | None = None,
        secrets_env_path: Path | str | None = None,
        **overrides: object,
    ) -> RegStackConfig:
        """Convenience constructor delegating to ``regstack.config.loader.load_config``."""
        from regstack.config.loader import load_config

        return load_config(
            toml_path=toml_path,
            secrets_env_path=secrets_env_path,
            **overrides,
        )
