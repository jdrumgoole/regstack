from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EmailBackend = Literal["console", "smtp", "ses"]
SmsBackend = Literal["null", "sns", "twilio"]
JwtAlgorithm = Literal["HS256", "HS384", "HS512"]
TokenTransport = Literal["bearer", "cookie"]


class EmailConfig(BaseModel):
    backend: EmailBackend = "console"
    from_address: EmailStr = "noreply@example.com"
    from_name: str = "RegStack"

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

    ses_region: str = "eu-west-1"
    ses_profile: str | None = None


class SmsConfig(BaseModel):
    backend: SmsBackend = "null"
    from_number: str | None = None

    sns_region: str = "eu-west-1"

    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None


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
    cookie_domain: str | None = None
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

    # Reserved for future route-level rate limiting (slowapi-style).
    login_max_per_minute: Annotated[int, Field(ge=1)] = 5
    login_max_per_hour: Annotated[int, Field(ge=1)] = 20

    # Sub-configs
    email: EmailConfig = Field(default_factory=EmailConfig)
    sms: SmsConfig = Field(default_factory=SmsConfig)

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

    @field_validator("jwt_secret")
    @classmethod
    def _warn_empty_secret(cls, v: SecretStr) -> SecretStr:
        # An empty secret is allowed at construction time so defaults remain
        # usable in tests; production callers should populate it explicitly
        # or via the wizard. Validation that *requires* it lives at the
        # RegStack façade boundary so test fixtures can opt out.
        return v

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
