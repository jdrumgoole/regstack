# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/) once `1.0.0` ships.

## 0.2.0 — unreleased

**Multi-backend support.** SQLite is now the default; Postgres and
MongoDB are switched in by changing `database_url`. Embedding API
breaking change: `RegStack(config=, db=)` → `RegStack(config=,
backend=None)`; the backend is auto-built from the URL scheme.

### Added

- `regstack.backends.protocols` — `Protocol` classes for the five repos
  plus the shared `UserAlreadyExistsError`.
- `regstack.backends.base.Backend` ABC and
  `regstack.backends.factory.build_backend(config)` URL-scheme router.
- `regstack.backends.mongo` — relocated Mongo code with a `MongoBackend`
  class.
- `regstack.backends.sql` — new SQLAlchemy 2 async backend driving
  SQLite (aiosqlite) and Postgres (asyncpg). Five protocol-conforming
  repos, `UtcDateTime` TypeDecorator for cross-database tz-aware
  datetimes, `install_schema()` that creates all tables idempotently.
- `RegStack.aclose()` to tear down the backend's connection pool.
- `regstack.backends.protocols.UserRepoProtocol.purge_expired(...)` on
  every repo so SQL backends can drive cleanup uniformly (Mongo still
  relies on TTL indexes in normal operation).
- `examples/sqlite/`, `examples/postgres/`, `examples/mongo/` — one
  demo per backend sharing a FastAPI scaffold under `examples/_common/`.

### Changed

- `RegStackConfig.mongodb_url` → `database_url`. Default is
  `sqlite+aiosqlite:///./regstack.db`. `mongodb_database` retained for
  Mongo URLs without a `/dbname` path.
- `RegStack.install_indexes()` → `install_schema()` (alias kept).
- `UserRepo.count(filter_=...)` → `count(*, is_active=,
  is_verified=, is_superuser=)`.
- `UserRepo.list_paged(sort=...)` → `list_paged(*,
  sort_by_created_at_desc=)`.
- `MfaCodeRepo.find` returns `MfaCode | None` instead of `dict`.
- `regstack init` wizard asks which backend to use and writes the
  appropriate `database_url`.
- `regstack doctor` is backend-agnostic (`Backend.ping()`, schema check
  per backend kind).
- pyproject: `pymongo` and `asyncpg` moved to optional extras
  (`mongo` / `postgres`); SQLAlchemy + aiosqlite + Alembic in base deps.

## 0.1.1 — 2026-04-27

- Rewrite the README's relative links (`examples/minimal/`,
  `docs/security.md`, `LICENSE`, `SECURITY.md`, etc.) as absolute
  GitHub / Read the Docs URLs so they resolve on the PyPI project page,
  not just on GitHub. README-only release.

## 0.1.0 — 2026-04-27

First tagged release. Bundles M1–M6 from the development plan into a
single Apache-2.0 package on PyPI.

### M1 — skeleton

- `RegStack` façade, `RegStackConfig` (env + TOML loader), `BaseUser`,
  `UserRepo`, `BlacklistRepo`.
- JWT codec with per-purpose derived keys, per-token blacklist, bulk
  revocation via `tokens_invalidated_after`.
- Argon2 password hashing via `pwdlib`.
- JSON router: `register`, `login`, `logout`, `me`.
- Console email backend.
- `regstack init` wizard.
- `examples/minimal/` embedding demo.

### M2 — verification + reset

- Durable `pending_registrations` collection (hashed tokens, TTL).
- `verify`, `resend-verification`, `forgot-password`, `reset-password`.
- Login lockout (`LoginAttemptRepo` + `LockoutService` → 429 +
  `Retry-After`).
- SMTP backend (aiosmtplib) and SES backend (lazy aioboto3).
- `MailComposer` with Jinja2 `ChoiceLoader` for host-overridable email
  templates.

### M3 — account management + admin

- `PATCH /me`, `change-password`, `change-email` +
  `confirm-email-change`, `DELETE /account`.
- JSON admin router (`/admin/{stats,users,users/{id},users/{id}/resend-verification}`)
  behind `enable_admin_router`.
- `regstack create-admin` and `regstack doctor` CLIs.
- Float-precision JWT `iat` with `<=` bulk-revoke comparison so a login
  completing microseconds after a password / email change keeps its
  session.

### M4 — SSR pages + theming

- `ui_router` behind `enable_ui_router`: login, register, verify,
  forgot, reset, confirm-email-change, account dashboard.
- `core.css` + `theme.css` with CSS-custom-property theming, light +
  `prefers-color-scheme: dark`.
- Bundled `regstack.js` reads endpoints from `<body data-rs-api
  data-rs-ui>`.
- `theme_css_url` for stylesheet override; `add_template_dir` for full
  template overrides (shared with the email composer).
- CSP-friendly: no inline `<style>` or `style="…"`.

### M5 — SMS + optional 2FA

- `SmsService` ABC with `null` / `sns` / `twilio` backends.
- Phone routes (`/phone/start`, `/phone/confirm`, `DELETE /phone`).
- Two-step MFA login: `mfa_required` response on `/login` →
  `/login/mfa-confirm` with the SMS code.
- `MfaCodeRepo` with hashed 6-digit codes, attempt tracking, TTL on
  `expires_at`, unique on `(user_id, kind)`.
- SSR `mfa-confirm` page and "SMS two-factor authentication" section
  on `/account/me` (set up + disable).
- E.164 phone validation.

### M6 — docs + CI + release

- Sphinx documentation (markdown via myst-parser, Furo theme).
- Quickstart, configuration, architecture, security, embedding,
  theming, CLI, and API reference pages.
- GitHub Actions: parallel test matrix on push/PR; OIDC PyPI publish
  on `v*` tags.
- `CHANGELOG.md` and `SECURITY.md`.
