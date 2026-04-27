# Architecture

regstack is a single embeddable façade — `RegStack` — that wires together
storage, password and JWT primitives, an email service, an SMS service,
a hooks bus, and a FastAPI router. Hosts construct one façade per
application and mount its router(s) wherever they like.

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

## One façade per process

`RegStack(config=…, backend=None, clock=…, email_service=…,
sms_service=…, mail_composer=…)` is the only public constructor.
Everything downstream (routers, dependencies, repos) takes its
dependencies from this instance, so you can run **two regstack instances
in the same process** without shared state — useful for multi-tenant
deployments where a single FastAPI app serves multiple
`<host, database, branding>` triples.

The backend is auto-built from ``config.database_url`` if not supplied
explicitly. URL scheme decides:
``sqlite+aiosqlite://`` → SQLAlchemy backend in SQLite mode.
``postgresql+asyncpg://`` → SQLAlchemy backend in Postgres mode.
``mongodb://`` / ``mongodb+srv://`` → Mongo backend.

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
- `backend` — the active :class:`~regstack.backends.base.Backend`.
- `install_schema()` — install indexes (Mongo) or run migrations (SQL).
  ``install_indexes()`` kept as a backwards-compat alias.
- `aclose()` — tear down the backend's connection pool.
- `bootstrap_admin(email, password)`,
  `add_template_dir(path)`, `set_email_backend(...)`,
  `set_sms_backend(...)`, `on(event, handler)`.

## Backend abstraction

The Backend ABC owns the persistence story. Each backend ships:

- One concrete repository per protocol
  (``UserRepoProtocol``, ``PendingRepoProtocol``,
  ``BlacklistRepoProtocol``, ``LoginAttemptRepoProtocol``,
  ``MfaCodeRepoProtocol``).
- ``install_schema()`` to create indexes (Mongo) or run table creation
  / Alembic migrations (SQL).
- ``ping()`` for ``regstack doctor`` health checks.
- ``aclose()`` for clean shutdown.

The Mongo backend lives at ``regstack.backends.mongo``; the SQL
backend (driving both SQLite and Postgres via SQLAlchemy 2 async)
lives at ``regstack.backends.sql``. Both are routed via the
``regstack.backends.factory.build_backend(config)`` factory.

### TTL handling differences

Mongo gets free expiry via TTL indexes — `pending_registrations`,
`token_blacklist`, `login_attempts`, and `mfa_codes` all have
``expireAfterSeconds`` indexes that the Mongo background task reaps.

The SQL backends have no equivalent. Two safety nets:

- **Read-side filtering**: every query that pulls a "live" row also
  checks ``expires_at > now()`` (or equivalent). A stale row in the
  table is harmless because it's never returned.
- **Periodic reaper**: each repo exposes ``purge_expired(...)``. Hosts
  that care about disk usage can run it on a schedule (e.g. via
  APScheduler or a `regstack reap` cron job).

This means SQL backends are functionally correct without the reaper,
but accumulate dead rows over time. Mongo doesn't.

## Repositories

Each backend ships a thin async repo per collection / table. The repos
are all tz-aware
because `make_client` configures `AsyncMongoClient(..., tz_aware=True)`
— every layer above assumes UTC-aware datetimes.

`UserRepo` accepts an injected `Clock`; bulk-revoke writes
(`update_password`, `update_email`, `set_tokens_invalidated_after`) come
from the same clock as the JWT codec, so frozen-clock tests stay
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

`ui_router` mounts the same conditional pages.

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

Hosts are free to subscribe to custom event names too — the registry is
just a `defaultdict(list)`.

## Templating

Two Jinja2 environments share one mechanism:

- `MailComposer` — email templates under `regstack/email/templates/`.
- `build_ui_environment` — SSR HTML templates under
  `regstack/ui/templates/`.

Both wrap a `ChoiceLoader([host_dirs..., regstack_default])` so a host
override drops a same-named file into its template directory and wins
over the bundled version. `RegStack.add_template_dir(path)` feeds both
loaders simultaneously.

## CLI runtime

`regstack init`, `regstack create-admin`, and `regstack doctor` share
`cli/_runtime.py`. `open_regstack(toml_path=None)` is an async context
manager that builds a real RegStack against a real Mongo connection,
runs `install_indexes()`, and tears the connection down on exit — the
right pattern for a short-lived CLI invocation.

## Testing seams

- `Clock` protocol with `SystemClock` (production) and `FrozenClock`
  (tests). `JwtCodec` and `UserRepo` honour the injected clock so
  `frozen_clock.advance(timedelta(...))` deterministically advances
  expirations.
- `ConsoleEmailService` records messages in `outbox` so tests assert on
  rendered content. `NullSmsService` does the same for SMS.
- `make_client` factory fixture in `tests/conftest.py` lets a single
  test build multiple `RegStack` instances against per-worker DBs to
  exercise different config combinations without leaking.
