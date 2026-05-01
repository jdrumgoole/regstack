# API reference

Auto-generated from docstrings. The most useful entry point is
[`regstack.RegStack`](#regstack.app.RegStack); everything else hangs
off it.

This page is organized by what you'd reach for, not by package
hierarchy. Each section starts with a one-paragraph orientation,
then the ``autoclass`` / ``autofunction`` directives pull the
docstrings, signatures, and parameter docs straight off the package
source.

## Top-level

The handful of things you import from `regstack` directly:

- [`RegStack`](#regstack.app.RegStack) — the embeddable façade.
- [`RegStackConfig`](#regstack.config.schema.RegStackConfig) — top-level config.
- [`EmailConfig`](#regstack.config.schema.EmailConfig) — email-backend sub-config.
- [`SmsConfig`](#regstack.config.schema.SmsConfig) — SMS-backend sub-config.
- [`OAuthConfig`](#regstack.config.schema.OAuthConfig) — OAuth provider sub-config.

Most embeddings need only `RegStack` and `RegStackConfig`.

## Façade

`RegStack` is the embeddable façade. One per FastAPI application;
hosts mount its `router` and (optionally) `ui_router`. All collaborators
— password hasher, JWT codec, repos, hooks bus, email and SMS
services — hang off the instance.

```{eval-rst}
.. autoclass:: regstack.app.RegStack
   :members:
   :show-inheritance:
```

## Configuration

`RegStackConfig` is a `pydantic-settings` model loaded from
environment variables (`REGSTACK_*`), an optional
`regstack.secrets.env`, an optional `regstack.toml`, and programmatic
kwargs — in that priority order. See the
[Configuration guide](configuration.md) for every field with its
default.

```{eval-rst}
.. autoclass:: regstack.config.schema.RegStackConfig
   :members:
   :show-inheritance:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.config.schema.EmailConfig
   :members:
   :show-inheritance:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.config.schema.SmsConfig
   :members:
   :show-inheritance:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.config.schema.OAuthConfig
   :members:
   :show-inheritance:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autofunction:: regstack.config.loader.load_config
```

## Auth primitives

The pieces that make authentication work: password hashing (Argon2id),
JWT issuance and validation with per-purpose derived keys, login
lockout, and the FastAPI dependency factory. Hosts rarely instantiate
these directly — they're built and wired by the `RegStack` constructor
— but the docstrings on the public methods explain the contract.

```{eval-rst}
.. autoclass:: regstack.auth.password.PasswordHasher
   :members:

.. autoclass:: regstack.auth.jwt.JwtCodec
   :members:

.. autoclass:: regstack.auth.jwt.TokenPayload
   :members:

.. autoexception:: regstack.auth.jwt.TokenError

.. autofunction:: regstack.auth.jwt.is_payload_bulk_revoked

.. autoclass:: regstack.auth.lockout.LockoutService
   :members:

.. autoclass:: regstack.auth.lockout.LockoutDecision
   :members:

.. autoclass:: regstack.auth.dependencies.AuthDependencies
   :members:
```

### Time

Every time-sensitive operation reads `now()` through a `Clock`. Tests
inject `FrozenClock` to make assertions deterministic.

```{eval-rst}
.. autoclass:: regstack.auth.clock.Clock
   :members:

.. autoclass:: regstack.auth.clock.SystemClock
   :members:

.. autoclass:: regstack.auth.clock.FrozenClock
   :members:
```

## Backends

regstack ships three storage backends behind one set of `Protocol`
classes: SQLite (default, no infrastructure), PostgreSQL, and MongoDB.
The active backend is auto-built from `config.database_url`'s URL
scheme via `build_backend`. Hosts that need to share a connection pool
with their own application can pass an explicit `Backend` to the
`RegStack` constructor.

```{eval-rst}
.. autoclass:: regstack.backends.base.Backend
   :members:
   :show-inheritance:

.. autoclass:: regstack.backends.base.BackendKind
   :members:

.. autofunction:: regstack.backends.factory.build_backend

.. autoclass:: regstack.backends.mongo.MongoBackend
   :members:
   :show-inheritance:

.. autoclass:: regstack.backends.sql.SqlBackend
   :members:
   :show-inheritance:
```

### Repository protocols

The five repository protocols are the contract every backend
implements. Routers and services depend only on these — switching
backends is a wiring change, not a code change.

```{eval-rst}
.. autoclass:: regstack.backends.protocols.UserRepoProtocol
   :members:

.. autoclass:: regstack.backends.protocols.PendingRepoProtocol
   :members:

.. autoclass:: regstack.backends.protocols.BlacklistRepoProtocol
   :members:

.. autoclass:: regstack.backends.protocols.LoginAttemptRepoProtocol
   :members:

.. autoclass:: regstack.backends.protocols.MfaCodeRepoProtocol
   :members:

.. autoclass:: regstack.backends.protocols.MfaVerifyOutcome
   :members:

.. autoclass:: regstack.backends.protocols.MfaVerifyResult
   :members:

.. autoexception:: regstack.backends.protocols.UserAlreadyExistsError

.. autoexception:: regstack.backends.protocols.PendingAlreadyExistsError
```

## Models

The persisted data shapes. `BaseUser` is the canonical user document;
`UserCreate` validates registration input; `UserPublic` is what the
API returns (omits the password hash). The other models drive
verification, lockout, and SMS MFA.

```{eval-rst}
.. autoclass:: regstack.models.user.BaseUser
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.user.UserCreate
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.user.UserPublic
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.pending_registration.PendingRegistration
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.login_attempt.LoginAttempt
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.mfa_code.MfaCode
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields
```

## Email + SMS

Pluggable transports for the verification / reset / change-email
emails and the SMS MFA codes. Implement `EmailService` or `SmsService`
to plug in a provider that isn't bundled (Postmark, SendGrid,
MessageBird, …) and pass the instance to `regstack.set_email_backend`
/ `set_sms_backend`.

### Email

```{eval-rst}
.. autoclass:: regstack.email.base.EmailMessage
   :members:

.. autoclass:: regstack.email.base.EmailService
   :members:

.. autoclass:: regstack.email.console.ConsoleEmailService
   :members:

.. autoclass:: regstack.email.composer.MailComposer
   :members:

.. autofunction:: regstack.email.factory.build_email_service
```

### SMS

```{eval-rst}
.. autoclass:: regstack.sms.base.SmsMessage
   :members:

.. autoclass:: regstack.sms.base.SmsService
   :members:

.. autofunction:: regstack.sms.base.is_valid_e164

.. autoclass:: regstack.sms.null.NullSmsService
   :members:

.. autofunction:: regstack.sms.factory.build_sms_service
```

## OAuth

Opt-in subsystem behind `enable_oauth` and the `oauth` extra. v1
ships Google; the abstraction is shaped so adding GitHub /
Microsoft / Apple later is a new module under
`regstack.oauth.providers` plus a registry entry. The full host
guide is in [OAuth](oauth.md); the threat model is in
[Security model](security.md#oauth-sign-in-with-google).

### Provider abstraction

```{eval-rst}
.. autoclass:: regstack.oauth.base.OAuthProvider
   :members:

.. autoclass:: regstack.oauth.base.OAuthTokens
   :members:

.. autoclass:: regstack.oauth.base.OAuthUserInfo
   :members:

.. autoclass:: regstack.oauth.registry.OAuthRegistry
   :members:

.. autoexception:: regstack.oauth.errors.OAuthError

.. autoexception:: regstack.oauth.errors.OAuthConfigError

.. autoexception:: regstack.oauth.errors.OAuthTokenExchangeError

.. autoexception:: regstack.oauth.errors.OAuthIdTokenError
```

### Google provider

```{eval-rst}
.. autoclass:: regstack.oauth.providers.google.GoogleProvider
   :members:
   :show-inheritance:
```

### Identity + state storage

```{eval-rst}
.. autoclass:: regstack.models.oauth_identity.OAuthIdentity
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.models.oauth_state.OAuthState
   :members:
   :exclude-members: model_config, model_fields, model_computed_fields

.. autoclass:: regstack.backends.protocols.OAuthIdentityRepoProtocol
   :members:

.. autoclass:: regstack.backends.protocols.OAuthStateRepoProtocol
   :members:

.. autoexception:: regstack.backends.protocols.OAuthIdentityAlreadyLinkedError
```

### Router

```{eval-rst}
.. autofunction:: regstack.routers.oauth.build_oauth_router
```

## Hooks

The event bus regstack uses to fire side-effect notifications
(`user_registered`, `password_changed`, etc.) without coupling auth
code to host concerns like CRMs or analytics. See the
[Embedding guide](embedding.md#subscribing-to-events) for examples.

```{eval-rst}
.. autoclass:: regstack.hooks.events.HookRegistry
   :members:

.. autodata:: regstack.hooks.events.KNOWN_EVENTS
```

## Routers

Hosts normally access these via the `regstack.router` and
`regstack.ui_router` properties; the builder functions are public for
hosts that want to compose differently.

```{eval-rst}
.. autofunction:: regstack.routers.build_router

.. autofunction:: regstack.ui.pages.build_ui_router

.. autofunction:: regstack.ui.pages.build_ui_environment

.. autofunction:: regstack.ui.pages.default_static_dir
```
