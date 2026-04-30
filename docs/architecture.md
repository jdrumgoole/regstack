# Architecture

This page is the "how is regstack put together" tour, aimed at someone
who wants to embed it, extend it, or contribute to it. If you only
want to use it, [Quickstart](quickstart.md) is shorter.

regstack is a single embeddable façade — `RegStack` — that wires
together storage, password and [JWT](https://datatracker.ietf.org/doc/html/rfc7519)
primitives, an email service, an SMS service, a [hooks bus](#hooks),
and a [FastAPI](https://fastapi.tiangolo.com/) router. Hosts
construct one façade per application and mount its router(s) wherever
they like.

```text
┌────────────────────────────────────────────────┐
│ Host FastAPI app                               │
│   app.include_router(regstack.router)          │
│   app.include_router(regstack.ui_router)       │
└────────────────────┬───────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────┐
│ RegStack façade                                │
│   RegStackConfig · Clock · HookRegistry        │
│   JwtCodec · PasswordHasher · LockoutService   │
│   MailComposer · EmailService · SmsService     │
│   Backend (Mongo / SQLite / Postgres)          │
│     ↳ Repos: User · Pending · Blacklist        │
│              LoginAttempt · MfaCode            │
└────────────────────┬───────────────────────────┘
                     │
        ┌────────────┼─────────────┐
        ▼            ▼             ▼
   ┌────────┐  ┌──────────┐  ┌──────────┐
   │ SQLite │  │ Postgres │  │ MongoDB  │
   │ (file) │  │ (asyncpg)│  │ (pymongo)│
   └────────┘  └──────────┘  └──────────┘
```

The pattern is the façade pattern: one object that owns and exposes
a coherent set of related sub-systems, so the host has a single
import to learn.

## One façade per process

`RegStack(config=…, backend=None, clock=…, email_service=…,
sms_service=…, mail_composer=…)` is the only public constructor.
Everything downstream (routers, dependencies, repos) takes its
dependencies from this instance, so you can run **two regstack
instances in the same process** without shared state — useful for
multi-tenant deployments where a single FastAPI app serves multiple
`<host, database, branding>` triples.

The backend is auto-built from `config.database_url` if not supplied
explicitly. URL scheme decides:

- `sqlite+aiosqlite://` → SQLAlchemy backend in SQLite mode.
- `postgresql+asyncpg://` → SQLAlchemy backend in Postgres mode.
- `mongodb://` / `mongodb+srv://` → Mongo backend.

The façade exposes:

- `router` — the JSON `APIRouter` to mount under `config.api_prefix`.
- `ui_router` — the SSR `APIRouter` (built on first access; only
  meaningful when `enable_ui_router=True`).
- `static_files` — Starlette `StaticFiles` over the bundled CSS/JS.
- `deps` — `AuthDependencies` factory for `current_user` /
  `current_admin` (each call returns a closure-bound dep).
- `users`, `pending`, `blacklist`, `attempts`, `mfa_codes` — repos.
- `lockout`, `mail`, `jwt`, `password_hasher`, `hooks`, `email`, `sms` —
  collaborators.
- `backend` — the active `regstack.backends.base.Backend`.
- `install_schema()` — install indexes (Mongo) or run
  [Alembic](https://alembic.sqlalchemy.org/) migrations (SQL).
- `aclose()` — tear down the backend's connection pool.
- `bootstrap_admin(email, password)`,
  `add_template_dir(path)`, `set_email_backend(...)`,
  `set_sms_backend(...)`, `on(event, handler)`.

## Backend abstraction

The Backend ABC owns the persistence story. Each backend ships:

- One concrete repository per Protocol: `UserRepoProtocol`,
  `PendingRepoProtocol`, `BlacklistRepoProtocol`,
  `LoginAttemptRepoProtocol`, `MfaCodeRepoProtocol`.
- `install_schema()` to create indexes (Mongo) or run table creation /
  Alembic migrations (SQL).
- `ping()` for `regstack doctor` health checks.
- `aclose()` for clean shutdown.

The Mongo backend lives at `regstack.backends.mongo`; the SQL backend
(driving both SQLite and Postgres via [SQLAlchemy](https://www.sqlalchemy.org/)
async) lives at `regstack.backends.sql`. Both are routed via the
`regstack.backends.factory.build_backend(config)` factory.

### TTL handling differences

Mongo gets free expiry via TTL indexes — `pending_registrations`,
`token_blacklist`, `login_attempts`, and `mfa_codes` all have
`expireAfterSeconds` indexes that the Mongo background task reaps.

The SQL backends have no equivalent. Two safety nets:

- **Read-side filtering**: every query that pulls a "live" row also
  checks `expires_at > now()` (or equivalent). A stale row in the
  table is harmless because it's never returned.
- **Periodic reaper**: each repo exposes `purge_expired(...)`. Hosts
  that care about disk usage can run it on a schedule (e.g. a cron
  job calling a small `regstack reap` script).

This means SQL backends are functionally correct without the reaper,
but accumulate dead rows over time. Mongo doesn't.

## Repositories

Each backend ships a thin async repo per collection / table. The
Mongo repos are tz-aware because `make_client` configures
`AsyncMongoClient(..., tz_aware=True)`; the SQL repos use a custom
`UtcDateTime` SQLAlchemy `TypeDecorator` that stores UTC and
re-attaches the UTC tzinfo on read. Every layer above the repo
assumes UTC-aware datetimes — there is no naive datetime anywhere in
the public API.

`UserRepo` accepts an injected `Clock`; bulk-revoke writes
(`update_password`, `update_email`, `set_tokens_invalidated_after`)
come from the same clock as the JWT codec, so frozen-clock tests stay
consistent across the read/write boundary.

## Routers are built per-instance

Routers are not module-level singletons. `build_router(rs)` constructs
an `APIRouter` whose endpoints close over the specific `RegStack`
instance. This is how two regstacks can coexist in one process.

The composite `router` conditionally includes:

- `register`, `verify`, `login`, `logout`, `account` — always.
- `password` (forgot/reset) — when `enable_password_reset`.
- `phone` and the `mfa-confirm` route — when `enable_sms_2fa`.
- `admin` — when `enable_admin_router`.
- `oauth` — when `enable_oauth` AND at least one provider is
  registered on `rs.oauth`.

`ui_router` mounts the same conditional pages, plus
`/account/oauth-complete` when `enable_oauth` is on.

## OAuth subsystem

Opt-in. Lives in `regstack.oauth/`; hosts pull it in via the
`oauth` extra (`pyjwt[crypto]>=2.8`). Imports are lazy — the
package keeps importing on a base install with no `cryptography`
installed, and the OAuth-specific modules only get loaded when
`enable_oauth` is on.

The shape is:

- `OAuthProvider` ABC — three methods: `authorization_url`,
  `exchange_code`, `verify_id_token`.
- `OAuthRegistry` — name-keyed map of providers, scoped to one
  `RegStack` instance. The `RegStack` constructor reads
  `config.oauth` and registers `GoogleProvider` automatically when
  `enable_oauth` and the credentials are set; hosts can also
  register custom providers post-construction.
- `GoogleProvider` — Authorization Code with PKCE, ID-token
  verification via `pyjwt[crypto]` + `PyJWKClient` against Google's
  JWKS. ~150 lines hand-rolled rather than pulling `authlib`.
- Two new repos via the protocol pattern:
  `OAuthIdentityRepoProtocol` (links between regstack users and
  external accounts; double-unique on `(provider, subject_id)` and
  `(user_id, provider)`) and `OAuthStateRepoProtocol` (in-flight
  state rows carrying the PKCE `code_verifier`, redirect target,
  mode, and the `result_token` slot the SPA exchanges).
- `build_oauth_router(rs)` — the router with the five endpoints
  (`/start`, `/callback`, `/exchange`, `/link/start`, `/link`) plus
  `/oauth/providers` for the SSR connected-accounts panel.

The token-handoff round-trip avoids putting access tokens in URLs:
the callback stashes the freshly-minted session JWT on the state
row's `result_token`, redirects to `/account/oauth-complete?id=…`,
and the SPA POSTs that id back to `/oauth/exchange` to retrieve the
token. The exchange consumes the row atomically — the same id can't
be exchanged twice.

The full design (including the four-milestone build sequence and
the threat model) is in
[`tasks/oauth-design.md`](https://github.com/jdrumgoole/regstack/blob/main/tasks/oauth-design.md).

## Hooks

`HookRegistry.fire(event, **kwargs)` runs every registered handler
concurrently and **swallows exceptions** (logged via
`log.exception`). A failing notification handler must never break a
primary auth flow. Known events:

- `user_registered`
- `user_logged_in` / `user_logged_out`
- `user_verified` / `verification_requested`
- `password_reset_requested` / `password_reset_completed` / `password_changed`
- `email_change_requested` / `email_changed`
- `phone_setup_started` / `mfa_login_started`
- `mfa_enabled` / `mfa_disabled`
- `user_deleted`
- `oauth_signin_started` / `oauth_signin_completed`
- `oauth_account_linked` / `oauth_account_unlinked`

Hosts are free to subscribe to custom event names too — the registry
is just a `defaultdict(list)`. Use this surface to push events into
your CRM, mailing list, or analytics pipeline without modifying
regstack itself.

## Templating

Two [Jinja2](https://jinja.palletsprojects.com/) environments share
one mechanism:

- `MailComposer` — email templates under `regstack/email/templates/`.
- `build_ui_environment` — SSR HTML templates under
  `regstack/ui/templates/`.

Both wrap a `ChoiceLoader([host_dirs..., regstack_default])` so a
host override drops a same-named file into its template directory and
wins over the bundled version. `RegStack.add_template_dir(path)`
feeds both loaders simultaneously.

## CLI runtime

`regstack init`, `regstack create-admin`, and `regstack doctor` share
`cli/_runtime.py`. `open_regstack(toml_path=None)` is an async
context manager that builds a real RegStack against a real backend,
runs `install_schema()`, and tears the connection down on exit — the
right pattern for a short-lived CLI invocation.

## Testing seams

- `Clock` protocol with `SystemClock` (production) and `FrozenClock`
  (tests). `JwtCodec` and `UserRepo` honour the injected clock so
  `frozen_clock.advance(timedelta(...))` deterministically advances
  expirations.
- `ConsoleEmailService` records messages in `outbox` so tests assert
  on rendered content. `NullSmsService` does the same for SMS.
- `make_client` factory fixture in `tests/conftest.py` lets a single
  test build multiple `RegStack` instances against per-worker DBs to
  exercise different config combinations without leaking. The
  parametrized `backend_kind` fixture runs every integration test
  against every active backend in parallel via pytest-xdist.
