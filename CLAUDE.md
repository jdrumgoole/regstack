# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`regstack` is an embeddable account-management module for FastAPI / MongoDB
host apps. It exposes one `RegStack` façade that wires config, repos, JWT,
hooks, email, and a router; hosts mount `regstack.router` on their own
FastAPI app. Two existing apps (`winebox`, `putplace`) are the design inputs —
their requirements drove the API surface — but **no host integration code lives
here**. regstack ships standalone and the hosts adopt it later in their own
repos.

The full plan, including milestone scope and deferred items, lives at
`/Users/jdrumgoole/.claude/plans/we-want-to-create-mutable-turing.md`.

## Current milestone status

- **M1 — done.** Skeleton, config + TOML/env loader, BaseUser, UserRepo,
  BlacklistRepo, JWT (per-purpose derived secrets, per-token blacklist,
  bulk revoke), Argon2 password hashing, console email, register/login/me/logout
  router, `regstack init` wizard, `examples/minimal/` demo.
- **M2 — done.** Durable `pending_registrations` (hashed-token TTL store),
  email verification + resend endpoints, forgot-password + reset-password
  with bulk revoke, login lockout (`LoginAttemptRepo` + `LockoutService` →
  HTTP 429 + `Retry-After`), SMTP backend (aiosmtplib), SES backend (lazy
  aioboto3 import), `MailComposer` with Jinja2 `ChoiceLoader` so hosts can
  override any email template by dropping a same-named file into a registered
  template directory.
- **M3 — done.** Account management (`PATCH /me`, `POST /change-password`,
  `POST /change-email` + `POST /confirm-email-change`, `DELETE /account`),
  JSON admin router (`/admin/{stats,users,users/{id},users/{id}/resend-verification}`)
  conditionally mounted on `enable_admin_router`, `regstack create-admin` CLI
  (idempotent), `regstack doctor` CLI (jwt secret, mongo ping, indexes,
  email factory; opt-in DNS + send-test-email). Float-precision JWT `iat`
  with `<=` bulk-revoke comparison so a login completing microseconds after
  a password / email change keeps its session.
- **M4 — done.** SSR `ui_router` (mounted on `enable_ui_router`) with
  Jinja2 pages: `login`, `register`, `forgot`, `reset`, `verify`,
  `confirm-email-change`, `me`. `core.css` (structural only) +
  `theme.css` (CSS custom properties, light + `prefers-color-scheme: dark`).
  Hosts override visuals by serving their own `theme.css` and pointing
  `config.theme_css_url` at it (loaded after the bundled defaults), or by
  registering a template directory via `regstack.add_template_dir(path)`
  for full template overrides. Bundled `regstack.js` is the only client
  code — reads endpoints from `<body data-rs-api data-rs-ui>` so it works
  for any prefix layout. CSP-friendly (no inline `<style>` or `style="…"`
  attributes anywhere).
- **M5 — done.** SMS abstraction (`SmsService` ABC with `null` /
  `sns` (lazy `aioboto3`) / `twilio` (lazy SDK) backends). Phone routes
  (`POST /phone/start`, `POST /phone/confirm`, `DELETE /phone`) and a
  two-step login flow (`POST /login` returns `mfa_required` + a short-lived
  `mfa_pending_token`; `POST /login/mfa-confirm` completes with the SMS
  code). Mounted only when `enable_sms_2fa=True`. `MfaCodeRepo` stores
  hashed 6-digit codes with attempts tracking, TTL on `expires_at`, unique
  on `(user_id, kind)` so a re-issue overwrites a previous code. SSR
  picked up an `mfa-confirm` page and a "SMS two-factor authentication"
  section on `/account/me` (set up + disable). E.164 phone validation.
  Phone setup uses a separate signed `phone_setup` JWT carrying the
  proposed phone as a custom claim — same per-purpose key derivation as
  password-reset and email-change.
- **OAuth** — explicitly deferred. The `oauth/` package will hold a provider
  ABC only; concrete providers (Google first) come post-v1.

## Three kinds of single-use proof

When extending regstack, watch which of these to use:

- **Email verification token** — random 32-byte URL-safe string,
  SHA-256 hashed in `pending_registrations.token_hash`. Long TTL (24h
  default). The raw token only exists in the email body and the click URL.
- **Password-reset / email-change / phone-setup / login-MFA tokens** —
  signed JWTs with purpose-derived keys (`derive_secret(jwt_secret, purpose)`).
  Carry whatever extra claim the flow needs (`new_email`, `phone`). Short
  TTL (5–60 min). No DB row.
- **SMS one-time codes** — 6-digit numeric, SHA-256 hashed in
  `mfa_codes.code_hash`. Short TTL (5 min default). Per-user-per-kind
  unique so a re-issued code overwrites the old one. `attempts` field
  bounds brute force; the row is deleted on success or after
  `max_attempts` wrong guesses. The pending JWT (purpose `login_mfa` or
  `phone_setup`) carries `sub=user_id` and is what links the second-step
  request to the right code in the DB.

## Commands

All commands assume `uv` and a local MongoDB on `mongodb://localhost:27017`.

```bash
uv sync --extra dev                            # install + dev extras
uv run python -m invoke test                   # parallel pytest (xdist auto)
uv run python -m invoke test -k <expr>         # filter by node-id substring
uv run python -m invoke test-serial            # serial run (diagnose flakes)
uv run python -m invoke lint                   # ruff check + format check + mypy
uv run python -m invoke fmt                    # ruff format + ruff check --fix
uv run python -m invoke run-example            # boot examples/minimal on :8000
uv run regstack init                           # interactive wizard
```

Run a single test file or test:

```bash
uv run python -m pytest tests/integration/test_happy_path.py -k test_token_expires -vv
```

Boot the demo with an ephemeral JWT secret (no wizard needed):

```bash
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
export REGSTACK_MONGODB_URL=mongodb://localhost:27017
uv run uvicorn examples.minimal.main:app --reload --port 8000
```

## Architecture (the parts that need cross-file reading)

### One façade, instance-scoped dependencies

`RegStack` (`src/regstack/app.py`) is constructed once per host app and owns
all collaborators: `PasswordHasher`, `JwtCodec`, `UserRepo`, `BlacklistRepo`,
`HookRegistry`, an `EmailService`, and an `AuthDependencies` factory.

FastAPI dependencies (`current_user`, `current_admin`) are produced by
`AuthDependencies.current_user()` — this returns a closure-bound callable so
two `RegStack` instances in the same process don't share state via module
globals. Routers receive the `RegStack` instance and capture it in closures
(`build_router(rs)` in `routers/__init__.py`), so a router never depends on a
module-level singleton.

### Configuration loading

`RegStackConfig` (pydantic-settings v2) loads with priority:
**kwargs > env vars > secrets.env > TOML > defaults.** The merge happens in
`config/loader.load_config`, which flattens TOML into the same `REGSTACK_*`
namespace pydantic-settings reads from `os.environ`, with `__` as the nested
delimiter (e.g. `REGSTACK_EMAIL__FROM_ADDRESS`).

`RegStackConfig.load(...)` is the convenience wrapper most call sites use.
For tests, pass `toml_path=Path("/dev/null")` and `secrets_env_path=Path("/dev/null")`
to suppress accidental discovery of a stray local config.

### JWT revocation: two complementary mechanisms

regstack supports both per-token and bulk revocation. Both are checked on
every authenticated request, in `AuthDependencies._authenticate`:

1. **Per-token blacklist** — `BlacklistRepo` stores `{jti, exp}`. Logout
   inserts a row; the dependency rejects any token whose `jti` is present.
   The `exp` field has a TTL index (`expireAfterSeconds=0`) so MongoDB
   reaps rows automatically once they would have expired anyway.
2. **Bulk revocation** — `User.tokens_invalidated_after` is a timestamp.
   Any token with `iat < tokens_invalidated_after` is rejected. Password
   changes (and similar "log everyone out" events) bump this field. This
   is O(1) at write time and avoids enumerating every outstanding `jti`.

JWT signing keys are **derived per purpose** (`session`, `verification`,
`password_reset`, …) from the master `jwt_secret` via HMAC-SHA256. Compromise
of one derived key does not compromise others. See
`config/secrets.derive_secret` and `JwtCodec._key`.

`JwtCodec` deliberately **disables pyjwt's `exp`/`iat` checks** and validates
both against the injected `Clock`. This is the seam that lets `FrozenClock`
fast-forward tests without monkeypatching `time.time`.

### Bulk revocation: float `iat` and `<=` cutoff

The bulk-revoke check in `is_payload_bulk_revoked` is `payload.iat <= cutoff`,
not `<`. Tokens issued at exactly the cutoff instant are revoked
(conservative — at the instant of a password change we don't know whether
the token came before or after); tokens issued microseconds later are valid.

To make "microseconds later" meaningful, regstack emits the JWT `iat` claim
as a float (RFC 7519 NumericDate explicitly allows this), so a login
completing in the same wall-clock second as a `change-password` /
`change-email` / `forgot-password` flow has an `iat` strictly greater than
the cutoff stored on the user document. Don't change `iat` back to `int` —
under integer-second `iat`, a same-second login would compare equal-or-less
to the microsecond-precision cutoff and be falsely revoked.

`UserRepo` is constructed with the same `Clock` as the JWT codec
(`UserRepo(db, collection_name, clock=self.clock)` in `RegStack.__init__`),
so `FrozenClock`-driven tests see consistent timestamps on both sides of
the comparison.

### MongoDB datetimes are tz-aware

`db.client.make_client` constructs `AsyncMongoClient(..., tz_aware=True)`.
Every other layer assumes UTC-aware datetimes. If you instantiate a client
elsewhere (in a script or extra test fixture), keep `tz_aware=True` or the
bulk-revocation comparison will raise `TypeError: can't compare offset-naive
and offset-aware datetimes`.

### Verification tokens are random + hashed; reset tokens are JWTs

These two flows look symmetric but use different mechanisms on purpose.

- **Verification** uses a 32-byte URL-safe random token. Only its SHA-256
  hash lives in `pending_registrations.token_hash` — a database read does
  not yield usable tokens. The `pending_registrations` collection is
  authoritative: TTL on `expires_at` reaps unused rows; resend
  `find_one_and_replace`s the row so the previous link silently dies.
- **Password reset** uses a JWT minted by `JwtCodec.encode(..., purpose="password_reset",
  ttl_seconds=config.password_reset_token_ttl_seconds)`. Validation reuses
  the regular decode path with the matching purpose. No DB row is needed
  because the token is self-contained and short-lived.

The reset endpoint always calls `users.update_password` (which bumps
`tokens_invalidated_after`) **plus** `lockout.clear` so a stolen-then-reset
session can't outlive the password change and the legitimate user isn't
still locked out from prior failed attempts.

### Anti-enumeration on `/forgot-password` and `/resend-verification`

Both endpoints return the same 202 response regardless of whether the email
exists. This deliberately prevents probing for valid accounts. The mail
itself is the only side-effect distinguishing the two cases. Don't add
"email not found" error paths to either route.

### Login lockout is sliding-window over a TTL-indexed collection

`login_attempts` has a TTL index whose `expireAfterSeconds` matches
`config.login_lockout_window_seconds` — Mongo reaps old failures
automatically, so `LockoutService.count_recent` is a simple count over docs
where `when >= now - window`. Successful login calls `lockout.clear(email)`
to wipe accumulated failures eagerly. When `rate_limit_disabled=True` (tests),
both `record_failure` and `check` short-circuit — no docs are written.

### Email-change uses a JWT with a custom claim, not a separate collection

`POST /change-email` mints a short-lived JWT (purpose `email_change`) that
carries the new address as a `new_email` custom claim — no separate
`pending_email_changes` collection. The encoder/decoder live in
`routers/account.py` (not `JwtCodec`) precisely because of the custom
claim; both still derive their signing key from
`config/secrets.derive_secret(jwt_secret, "email_change")`, so a session
token cannot satisfy the email-change endpoint and vice versa.

Confirmation calls `users.update_email`, which uniquely-constraints on
`email` at the DB level and bumps `tokens_invalidated_after`; the user has
to log in again with the new address.

### Hosts override email AND UI templates via a single directory

`RegStack.add_template_dir(path)` prepends to BOTH the email composer and
the SSR UI Jinja `ChoiceLoader`s. A host wanting a custom verification email
drops `verification.subject.txt` / `verification.html` / `verification.txt`
into its template dir; a host wanting a custom login page drops
`auth/login.html`. Jinja resolves host-first and falls back to regstack's
defaults for anything not overridden. The defaults live at
`src/regstack/email/templates/` and `src/regstack/ui/templates/`.

For visual changes that don't need template surgery, hosts ship a custom
`theme.css` (overriding the `--rs-*` CSS custom properties) and point
`config.theme_css_url` at where they serve it. The base template loads it
after the bundled `theme.css` so host values win without touching the
package. See `examples/minimal/branding/theme.css` for a wine-themed
example that flips every page from blue+sans to burgundy+serif by changing
only one stylesheet URL.

### SSR pages are stateless; the bundled `regstack.js` does the actual work

The HTML returned by `ui_router` contains forms but no auth state — the
templates render the same regardless of whether the user is signed in.
`regstack.js` reads `data-rs-api` and `data-rs-ui` from `<body>`, dispatches
on `data-rs-page`, submits forms via `fetch`, stores the access token in
`localStorage` under `regstack.access_token`, and redirects unauthenticated
users to `<ui_prefix>/login`. This avoids cookie-based sessions (and the
CSRF middleware that comes with them) while keeping the JSON API the
single source of truth for auth state.

The verify and confirm-email-change pages take their token from the
query-string and auto-POST it on page load — the user just clicks the link
in their email and waits a second for the success/failure banner.

### Hooks are best-effort

`HookRegistry.fire(event, **kwargs)` runs all registered handlers
concurrently and **swallows exceptions** (logged via `log.exception`). A
failing webhook side-effect must never break the primary auth flow. Known
event names live in `hooks/events.KNOWN_EVENTS`.

### Tests are parallel-safe by construction

`tests/conftest.py` gives each pytest-xdist worker a unique database name
(`regstack_test_{worker_id}_{random_hex}`) that is dropped at fixture
teardown. **Don't introduce session-scoped state, fixed ports, or shared
collections.** The full suite must pass under `pytest -n auto` reliably —
flaky tests are bugs, not noise. Time-dependent assertions use the
`frozen_clock` fixture, never `time.sleep` or wall-clock delays.

## Conventions

- **Python 3.11+.** Use `from __future__ import annotations` in every module.
- **Typehints on everything** (`mypy --strict` is wired up but not yet on CI).
- **No bash scripts.** All build/admin tasks go in `tasks.py` (invoke).
- **`uv run`** in front of every Python command.
- **Don't add a feature flag without a code path that uses it.** The
  `RegStackConfig` flags `enable_admin_router`, `enable_ui_router`,
  `enable_sms_2fa`, `enable_oauth` are reserved for their respective
  milestones — adding a flag is a future-milestone marker, not a stub.
- **Email backends:** `console`, `smtp` (aiosmtplib), and `ses` (lazy
  aioboto3) all ship as of M2. SES requires the `ses` extra
  (`uv sync --extra ses`); other backends are no-extra installs.
- **Comments are rare.** Add one only when *why* is non-obvious — a
  workaround, a constraint that isn't visible from the code, an invariant
  a future reader would otherwise break. Don't restate *what* the code does.
