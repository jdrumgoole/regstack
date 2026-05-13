# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/) once `1.0.0` ships.

## 0.5.7 — 2026-05-13

Documentation-only follow-up to 0.5.6 — no runtime code changes.

### Docs

- `docs/configuration.md` now documents the per-route `*_rate_limit`
  family (added in 0.5.4) instead of pointing at
  `login_max_per_minute` / `login_max_per_hour` as reserved future
  fields.
- `docs/security.md` no longer references
  `PasswordHasher.needs_rehash` (removed in 0.5.6). Replacement
  guidance points hosts at `pwdlib.PasswordHash.verify_and_update`
  inside a `user_logged_in` hook.
- Root `CHANGELOG.md` backfilled with 0.4.0 and 0.5.0 entries so it
  matches this file. The two changelogs are now in sync.

## 0.5.6 — 2026-05-13

A rollup release that consolidates 11 days of security-review
remediation, supply-chain hardening, a full `mypy --strict` pass,
and the per-route rate-limits feature.

### Added

- **Per-route IP rate limits.** Opt-in via the new `rate_limit` extra
  (or a host-supplied `slowapi.Limiter`) plus any of the new
  `RegStackConfig.*_rate_limit` fields (`login_rate_limit`,
  `register_rate_limit`, `forgot_password_rate_limit`,
  `reset_password_rate_limit`, `verify_rate_limit`,
  `resend_verification_rate_limit`, `change_password_rate_limit`,
  `change_email_rate_limit`, `confirm_email_change_rate_limit`,
  `delete_account_rate_limit`). Each accepts a slowapi-syntax string
  (`"5/minute"`, `"5/minute;20/hour"`).
- New constructor argument `RegStack(rate_limiter=...)`. When at
  least one `*_rate_limit` field is set, regstack expects either
  this argument or the `rate_limit` extra; failure to provide one
  raises `RuntimeError` on first access to `regstack.router` —
  failing closed beats silently disabling the protection. Hosts
  remain responsible for `app.state.limiter` and the
  `RateLimitExceeded` exception handler; slowapi owns the 429
  response shape.
- **`user_logged_out` hook now fires.** The event was listed in
  `KNOWN_EVENTS` since M1 but no router ever emitted it.
  `routers/logout.py` now fires `user_logged_out` (with a `user=`
  kwarg) immediately after the bearer token is revoked.

### Changed (security)

- **JWT 401 responses no longer leak the pyjwt error reason.**
  Replaced `f"Invalid token: {exc}"` with the static `"Invalid or
  expired token."`. The pyjwt error text disclosed *why* a token was
  rejected (signature mismatch, expired, malformed, audience
  mismatch) — useful signal for an attacker probing the auth
  surface.
- **OAuth sign-in honours `allow_registration=False`.** `/register`
  already did; the OAuth `_resolve_user` "brand-new account" branch
  did not, so an operator who disabled self-service signup still got
  accounts created via "Sign in with Google". The OAuth callback now
  redirects with `?error=registration_disabled` if no existing
  account matches and registration is disabled.
- **Admin `DELETE /admin/users/{id}` now cascades
  `oauth_identities`.** Matches the user-initiated `DELETE
  /account` flow; previously left orphan rows that blocked the
  Google subject from re-registering.
- **`POST /phone/start` and `DELETE /phone` guard against OAuth-only
  users.** Both endpoints previously crashed with HTTP 500 for
  users with `hashed_password=None`. Both now return 400 with a
  message pointing to forgot-password (which doubles as a "set
  initial password" path).

### Changed (BREAKING — hook contracts)

- **`mfa_login_started` and `phone_setup_started` no longer include
  the raw OTP code in their kwargs.** Hooks are best-effort
  observability and are the documented integration surface for
  analytics / logging / Slack notifications, so a plaintext OTP in
  `**kwargs` is a leak waiting to happen — a host adding
  `logger.info(kw)` to a hook handler is enough to put OTPs in a
  log stream. Hosts that subscribed to either event to take over
  SMS delivery should migrate to a custom `SmsService` subclass
  (the supported delivery override). The other kwargs (`user`,
  `phone`) remain.

### Changed (deps)

- `pyjwt>=2.12.1` (was `>=2.8`). Picks up CVE-2026-32597 (`crit`
  header bypass, CVSS 7.5).
- `cryptography>=46.0.7` added explicitly to the `oauth` extra
  (was pulled transitively, unbounded). Picks up CVE-2026-26007
  (ECC subgroup attack on the JWKS code path, CVSS 8.2) plus
  CVE-2026-34073 and CVE-2026-39892.
- `python-multipart>=0.0.26` (was `>=0.0.9`). Picks up
  CVE-2026-40347 (DoS via oversized multipart preamble).
- `pypa/gh-action-pypi-publish` in `publish.yml` pinned to a commit
  SHA instead of the mutable `release/v1` branch. The publish job
  holds `id-token: write`, so a tag/branch swap upstream would let
  an attacker push a malicious wheel under our OIDC identity.

### Removed

- `PasswordHasher.needs_rehash` — called pwdlib's non-existent
  `check_needs_rehash` and would `AttributeError` if anyone invoked
  it. No callers in src or tests. If you were planning to use it,
  call `pwdlib.PasswordHash.verify_and_update` directly.

### Internal

- 72 `mypy --strict` errors cleared across 35 files. `inv lint` is
  green end-to-end (ruff + mypy). Still local-only — not yet a CI
  gate.
- Mongo `BlacklistRepo.purge_expired` added (was missing from the
  Mongo impl; SQL impl already had it). Mongo's TTL index still
  reaps automatically; the explicit `delete_many` is for protocol
  parity and for tests that can't wait for the 60-second TTL
  monitor.
- `KNOWN_EVENTS` reconciled with reality: 7 previously-undeclared
  events added (`verification_requested`, `email_change_requested`,
  `email_changed`, `phone_setup_started`, `mfa_login_started`,
  `mfa_enabled`, `mfa_disabled`).
- `routers/_helpers.require_password_set` factored out of
  `routers/account.py` and reused in `routers/phone.py`.
- `AsyncDatabase[MongoDoc]` / `AsyncMongoClient[MongoDoc]`
  parameterized across the Mongo backend so pymongo's typed stubs
  are satisfied.

### Notes

- `LockoutService` (per-account, sliding-window failure counter) is
  unchanged and continues to defend `/login` against
  credential-stuffing against a single account. Per-route IP limits
  are orthogonal: they defend each endpoint against a single source
  IP spamming requests across many accounts.
- The previously-reserved `login_max_per_minute` /
  `login_max_per_hour` config fields are kept for back-compat but
  no longer have any effect. Switch to the per-route fields when
  you next touch your config.

## 0.5.0 — 2026-05-02

### Added

- **Theme designer.** `regstack theme design` opens a native pywebview
  window with controls for every `--rs-*` CSS custom property and a
  real-time preview of the bundled SSR widgets (sign-in form, success
  / error banners, danger-zone button). Saving writes
  `regstack-theme.css`; the designer round-trips values back into the
  form on next launch so iteration is non-destructive. `--print-only`
  mode takes repeatable `--var NAME=VALUE` pairs (with a `dark:`
  prefix for dark-scheme overrides) and writes the file headlessly.
  Lives in `regstack.wizard.theme_designer`; registered as a lazy
  Click subgroup so `regstack init` / `doctor` don't pay the
  pywebview/uvicorn import cost.
- "Why use regstack" pitch in `docs/index.md` updated to surface the
  two pywebview tools (`oauth setup` + `theme design`) as a
  distinguishing feature vs. fastapi-users / Auth0 / Keycloak.

### Docs

- New "About the examples" convention block at the top of
  `docs/index.md`. Every URL, email, smtp host, and admin command
  across the docs now extrapolates from the same fictional app at
  `app.example.com` with `<username>` / `<password>` placeholders —
  no more `user:pw@host/dbname` / `db.internal/myapp` mishmash.

## 0.4.0 — 2026-05-02

### Added

- **OAuth setup wizard.** `regstack oauth setup` opens a native
  webview window that walks an operator through registering a Google
  OAuth 2.0 client and merges the credentials into `regstack.toml` +
  `regstack.secrets.env` non-destructively (preserves comments, other
  tables, unrelated keys). 12-step SPA inside a local-only
  127.0.0.1 FastAPI server, gated by a per-launch random token. Each
  Next click hits a server-side validator so the Write step can never
  be reached with bad data. `--print-only` mode skips the GUI for
  headless / CI use.
- Three new base dependencies: `pywebview>=5.0`, `tomlkit>=0.13`,
  `uvicorn[standard]>=0.29` (the wizard's local server).
- `pytest-playwright` added to the `dev` extra; new `inv test-e2e`
  task chained into `inv test-all`.

## 0.3.0 — 2026-04-30

**OAuth — Sign in with Google.** Built across four PRs (M1–M4 of
[`tasks/oauth-design.md`](https://github.com/jdrumgoole/regstack/blob/main/tasks/oauth-design.md));
this is the release cut that wraps them up.

### Added

- New optional extra `oauth = ["pyjwt[crypto]>=2.8"]`.
- New `enable_oauth` flag and `OAuthConfig` sub-model
  (`google_client_id`, `google_client_secret`, `google_redirect_uri`,
  `auto_link_verified_emails`, `enforce_mfa_on_oauth_signin`,
  `state_ttl_seconds`, `completion_ttl_seconds`).
- `regstack.oauth` package — `OAuthProvider` ABC, `OAuthRegistry`,
  `OAuthTokens`, `OAuthUserInfo`, error hierarchy, and the concrete
  `GoogleProvider` (Authorization Code with PKCE, ID-token
  verification via `pyjwt[crypto]` + `PyJWKClient` against Google's
  JWKS).
- Five JSON endpoints (mounted lazily when `enable_oauth=True` and a
  provider is registered):
  - `GET    /oauth/{provider}/start`
  - `GET    /oauth/{provider}/callback`
  - `POST   /oauth/exchange`
  - `POST   /oauth/{provider}/link/start` (auth)
  - `DELETE /oauth/{provider}/link` (auth)
  - `GET    /oauth/providers` (auth)
- New SSR page `/account/oauth-complete` (token-handoff round-trip).
- "Sign in with Google" button on `/account/login` and a Connected-
  accounts panel on `/account/me`. Login page surfaces callback
  errors via `?error=<code>` with translated banners.
- Two new repo protocols: `OAuthIdentityRepoProtocol`,
  `OAuthStateRepoProtocol`. Mongo + SQL implementations with
  parametrized integration tests over all three backends.
- Four new hook events: `oauth_signin_started`,
  `oauth_signin_completed`, `oauth_account_linked`,
  `oauth_account_unlinked`.
- `tests/_fake_google/` — in-process provider stub so the OAuth
  test suite stays offline and parallel-safe.
- New docs page [`docs/oauth.md`](oauth.md) — host guide.

### Changed (potentially breaking)

- **`BaseUser.hashed_password: str` → `str | None`.** OAuth-only
  users have no password. The login route rejects password attempts
  on these accounts with the same generic 401 wrong-password gets
  (no enumeration). `change-password`, `change-email`, and
  `delete-account` all return 400 for OAuth-only users with a
  pointer at the password-reset flow, which doubles as a "set
  initial password" path.
- `users.hashed_password` is now nullable in the SQL schema —
  migration `0002_oauth.py` flips the column via `batch_alter_table`
  (SQLite-safe). Existing rows are unaffected.
- New SQL tables `oauth_identities` and `oauth_states`. Mongo
  collections + indexes added by `install_schema()`.

### Security defaults

- **Account-linking policy defaults to refuse.** When a Google
  sign-in carries an email that already belongs to a regstack user,
  the callback returns `?error=email_in_use`. Hosts can opt into
  auto-linking via `oauth.auto_link_verified_emails = true`, which
  also requires `email_verified=true` on the ID token. The threat
  model is in `tasks/oauth-design.md` § 1.
- **Server-side PKCE.** `code_verifier` is stored on the
  `oauth_states` row and never enters the URL.
- **One-time token-handoff.** `/oauth/exchange` consumes the state
  row atomically; second exchange returns 404.
- **Refuse to unlink the only sign-in method.** Returns 400 for
  OAuth-only users attempting to unlink their only provider.
- **OAuth sessions are normal session JWTs** — the existing
  `tokens_invalidated_after` bulk-revoke applies, so a password
  change kills any OAuth-issued session too.

### Migration notes

- Install the extra: `uv add 'regstack[oauth]'`.
- Configure: set `enable_oauth = true` and provide
  `oauth.google_client_id` + `oauth.google_client_secret` (the
  secret in `regstack.secrets.env` as
  `REGSTACK_OAUTH__GOOGLE_CLIENT_SECRET`).
- Schema: roll forward via `regstack migrate` or rely on
  `install_schema()` at first boot.

## 0.2.6 — 2026-04-28

**Bug fix.**

### Fixed

- ``/admin/stats`` reported ``pending_registrations: 0`` on every SQL
  backend. The route reached into the Mongo repo's private
  ``_collection`` attribute and silently fell back to ``0`` when the
  attribute was absent — the kind of failure that survives a
  multi-backend refactor when the integration tests don't pin the
  number.

### Added

- ``PendingRepoProtocol.count_unexpired(now=None) -> int``, with Mongo
  and SQL implementations. "Unexpired" rather than a raw row count
  because SQL backends accumulate dead rows until ``purge_expired``
  runs; an admin looking at "pending: 47" wants 47 *live* rows.
- The admin stats route now routes the count through
  ``rs.clock.now()``. Without this, ``FrozenClock``-driven tests
  would see every row as "expired" because the route would be reading
  wall-clock time while the rest of the system runs on the injected
  clock. Same shape of clock-injection drift the bulk-revoke fix
  closed earlier.
- New parametrized integration test
  ``test_stats_pending_registrations_count_unexpired`` runs against
  SQLite + Mongo + Postgres and confirms the count excludes expired
  rows on every backend.

## 0.2.5 — 2026-04-28

**Bug fix + tooling.**

### Fixed

- ``regstack doctor`` against a SQL backend crashed with
  ``asyncio.run() cannot be called from a running event loop``. The
  schema check called
  ``regstack.backends.sql.migrations.current()``, which used
  ``asyncio.run()`` internally — invalid inside doctor's own
  ``asyncio.run``. Added ``current_async()`` and switched the doctor
  command to use it. Sync ``current()`` is preserved for the migrate
  CLI (which runs outside an event loop).

### Added

- ``inv coverage [--no-html] [--fail-under=N]`` — runs the full
  three-backend matrix under coverage, combines per-pytest-xdist-worker
  ``.coverage`` files, prints the term-with-missing report, and writes
  ``htmlcov/``. Branch coverage is on by default.
- ``[tool.coverage.*]`` config in ``pyproject.toml``.
- ``tests/unit/test_cli_init.py`` — six tests driving the
  ``regstack init`` wizard via ``CliRunner(input=...)``. Lifts
  ``cli/init.py`` from 14% → 88%.
- ``tests/unit/test_cli_doctor.py`` — four tests for the SQLite
  ``regstack doctor`` paths. Lifts ``cli/doctor.py`` from 61% → 87%.

Total line coverage on the full backend matrix: **85% → 87.1%**
(branch coverage is also newly enabled).

## 0.2.4 — 2026-04-28

**Breaking** — every back-compat shim left over from the
multi-backend refactor has been removed.

### Removed

- `RegStack.install_indexes()` — the 0.1.x alias for
  `install_schema()`. Call `install_schema()`.
- `ObjectIdStr` alias for `IdStr` in `regstack.models._objectid`.
  Import `IdStr` directly.
- `__all__`-based re-exports of `UserAlreadyExistsError`,
  `PendingAlreadyExistsError`, `MfaVerifyOutcome`, and
  `MfaVerifyResult` from `regstack.backends.mongo.repositories.*` and
  the package `__init__`. Their canonical home is
  `regstack.backends.protocols`; that's where every consumer in the
  package itself already imports them.

### Migration

| Old                                                                              | New                                                            |
|----------------------------------------------------------------------------------|----------------------------------------------------------------|
| `await regstack.install_indexes()`                                               | `await regstack.install_schema()`                              |
| `from regstack.models._objectid import ObjectIdStr`                              | `from regstack.models._objectid import IdStr`                  |
| `from regstack.backends.mongo.repositories.user_repo import UserAlreadyExistsError`     | `from regstack.backends.protocols import UserAlreadyExistsError`     |
| `from regstack.backends.mongo.repositories.pending_repo import PendingAlreadyExistsError` | `from regstack.backends.protocols import PendingAlreadyExistsError`  |
| `from regstack.backends.mongo.repositories.mfa_code_repo import MfaVerifyOutcome, MfaVerifyResult` | `from regstack.backends.protocols import MfaVerifyOutcome, MfaVerifyResult` |

The internal Mongo helper
`regstack.backends.mongo.indexes.install_indexes(db, config)` is
unchanged — that's the function `MongoBackend.install_schema` calls
to actually create the indexes.

## 0.2.3 — 2026-04-28

**Docs-only release.** API reference rewritten around the current
package layout, public surface gained proper Google-style docstrings.

### Changed

- ``docs/api.md`` restructured around the post-multi-backend package
  layout (``regstack.backends.{base,protocols,factory,mongo,sql}`` and
  friends). Each section now opens with a one-paragraph orientation
  before the autodoc directives. The pre-refactor
  ``regstack.db.repositories.*`` references that rendered empty are
  gone.
- Added Google-style docstrings (purpose summary + Args / Returns /
  Raises) to the most-touched public methods on ``RegStack``,
  ``JwtCodec``, ``PasswordHasher``, ``LockoutService``,
  ``AuthDependencies``, ``HookRegistry``, ``EmailService``,
  ``SmsService``, ``build_router``, ``build_ui_router``,
  ``build_ui_environment``, ``default_static_dir``, ``Clock`` /
  ``SystemClock`` / ``FrozenClock``.
- Dataclass field documentation moved to PEP 258 attribute docstrings
  on ``TokenPayload``, ``LockoutDecision``, ``EmailMessage``,
  ``SmsMessage``, ``MfaVerifyResult`` — autodoc now renders each field
  with its description without the "duplicate object description"
  warnings the napoleon ``Attributes:`` block was triggering.
- ``MfaVerifyOutcome`` enum docstring reformatted as a bullet list
  (the napoleon ``Members:`` block isn't a recognised section).

## 0.2.2 — 2026-04-28

**Docs-only release.**

### Changed

- README and `docs/index.md` both now lead with the same pitch — a
  tagline ("Production-grade user accounts for your FastAPI app —
  without the vendor lock-in, the second service to run, or the
  homegrown auth bugs"), a "The problem regstack solves" section
  (Argon2, JWT revocation, account enumeration, bulk session
  invalidation, hashed one-time tokens, E.164 phone numbers), and a
  "Why not just use…?" comparison table covering hosted SaaS
  (Auth0 / Clerk / WorkOS / Stytch), self-hosted IAM (Keycloak /
  Authentik / Authelia / Ory Kratos), `fastapi-users`, and DIY.
- Trimmed hyperlink density back. Only major external packages,
  products, and JWT (RFC 7519) are linked. Wikipedia articles on
  CS concepts (façade pattern, multitenancy, idempotence, E.164,
  SHA-256, HMAC), MDN web platform basics (CSP, fetch, localStorage,
  HTTP 429, Retry-After, HTTPS, CSS custom properties), OWASP article
  links, Python stdlib pages, and deep-dependency helper-class docs
  (pwdlib, pydantic, asyncpg, pymongo, ChoiceLoader, TypeDecorator,
  StaticFiles, ProxyHeadersMiddleware, slowapi, APScheduler,
  pytest-xdist, Kubernetes probes) were removed.

## 0.2.1 — 2026-04-28

**Hotfix for 0.2.0.** `import regstack` was broken on any install that
didn't include the new `mongo` extra: `models/_objectid.py` imported
`bson` unconditionally, and four routers + the SQL MFA repo imported
shared error / enum types out of `regstack.backends.mongo.*`, which
in turn imports `pymongo` at module top level.

### Fixed

- `models/_objectid.py` now imports `bson.ObjectId` lazily inside a
  `try / except ImportError` and only uses it for `isinstance` checks
  when present.
- `UserAlreadyExistsError`, `PendingAlreadyExistsError`,
  `MfaVerifyOutcome`, and `MfaVerifyResult` moved from their backend
  modules to `regstack.backends.protocols` (the backend-agnostic
  location). Mongo modules re-export them for backwards compatibility.
- All consumer modules (`routers/register.py`, `routers/account.py`,
  `routers/login.py`, `routers/phone.py`, the SQL MFA repo) updated to
  import from `regstack.backends.protocols`.

### Added

- New `base-install-smoketest` CI job: builds the wheel and runs
  `import regstack` + a SQLite end-to-end RegStack lifecycle in a
  fresh venv with **no extras**. Will catch any future regression.
- New `tests/unit/test_base_install_imports.py` regression test that
  uses `sys.meta_path` to block `bson` / `pymongo` and confirm
  `import regstack` still succeeds.

## 0.2.0 — 2026-04-28

**Multi-backend support + Alembic migrations.** SQLite is now the
default; Postgres and
MongoDB are switched in by changing `database_url`. Embedding API
breaking change: `RegStack(config=, db=)` → `RegStack(config=,
backend=None)`; the backend is auto-built from the URL scheme.

This release also includes a documentation rewrite for less-expert
readers: the README and core docs now lead with the problem regstack
solves, hyperlink external standards (Argon2, RFC 7519, OWASP
enumeration, E.164, CSP, …), and compare regstack to the alternatives
(hosted SaaS, self-hosted IAM, `fastapi-users`, DIY).

### Added

- **Alembic migrations bundled.** `regstack.backends.sql.migrations`
  ships an in-package Alembic env (no `alembic.ini` on disk).
  `SqlBackend.install_schema()` runs `alembic upgrade head` instead of
  `MetaData.create_all`, so schema evolutions land as new revision
  files. New `regstack migrate [--target REV]` CLI for deploy-step
  migrations. New autogen-drift test catches `schema.py` ↔ migration
  mismatches before users see them.
- **`regstack doctor` schema check** is now Alembic-aware: it reports
  the deployed revision vs the bundled head and tells you to run
  `regstack migrate` if they diverge.
- **Per-backend invoke tasks**: `inv test-sqlite` (zero infra),
  `inv test-mongo` (needs local Mongo), `inv test-postgres
  [--url=...]` (needs local Postgres), `inv test-all` (all three).
  Driven by a new `REGSTACK_TEST_BACKENDS` env var that the
  parametrized backend fixture honours; mongo-only unit tests skip
  cleanly when mongo isn't in the active backend set.
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
